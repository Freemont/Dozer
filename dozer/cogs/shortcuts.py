"""Adds simple text-shortcuts to the bot"""
import codecs
import csv
import datetime
import io
import math
from io import BufferedIOBase, StringIO
from typing import List, Optional

import discord
from discord import Forbidden, app_commands
from discord.ext import commands
from discord.ext.commands import BadArgument, guild_only, has_permissions, CommandInvokeError

from dozer.context import DozerContext
from ._utils import *
from .. import db
from ..db import *


class ShortcutCategorySelect(discord.ui.Select):
    """Dropdown for selecting shortcut categories"""

    def __init__(self, categories: List[str], guild_id: int, settings_cache, cache):
        self.guild_id = guild_id
        self.settings_cache = settings_cache
        self.cache = cache

        options = []
        for category in categories:
            options.append(discord.SelectOption(
                label=category,
                description=f"View shortcuts in {category} category",
                value=category
            ))

        super().__init__(
            placeholder="Choose a category to view shortcuts...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle category selection"""
        selected_category = self.values[0]

        shortcuts = await ShortcutEntry.get_by(guild_id=self.guild_id, category=selected_category)
        settings = await self.settings_cache.query_one(guild_id=self.guild_id)

        if not shortcuts:
            embed = discord.Embed(
                title=f"Shortcuts - {selected_category}",
                description="No shortcuts found in this category!",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return

        page_size = settings.page_size if settings else 10
        paginator = ShortcutPaginator(shortcuts, selected_category, settings.prefix, self.guild_id, self.settings_cache, self.cache, page_size)
        embed = paginator.create_embed(0)

        await interaction.response.edit_message(embed=embed, view=paginator)


class ShortcutPaginator(discord.ui.View):
    """Paginated view for displaying shortcuts within a category"""

    def __init__(self, shortcuts: List, category: str, prefix: str, guild_id: int, settings_cache, cache, per_page: int = 10):
        super().__init__(timeout=300)  # 5 minute timeout
        self.shortcuts = shortcuts
        self.category = category
        self.prefix = prefix
        self.guild_id = guild_id
        self.settings_cache = settings_cache
        self.cache = cache
        self.per_page = per_page
        self.current_page = 0
        self.max_pages = math.ceil(len(shortcuts) / per_page)

        self.add_item(ShortcutBackButton(guild_id, settings_cache, cache))

        if self.max_pages > 1:
            self.add_item(ShortcutPreviousButton())
            self.add_item(ShortcutNextButton())

    def create_embed(self, page: int) -> discord.Embed:
        """Create embed for the current page"""
        start_idx = page * self.per_page
        end_idx = min(start_idx + self.per_page, len(self.shortcuts))
        page_shortcuts = self.shortcuts[start_idx:end_idx]

        embed = discord.Embed(
            title=f"Shortcuts - {self.category}",
            color=discord.Color.blue()
        )

        for shortcut in page_shortcuts:
            value = shortcut.value
            if len(value) > 1024:
                value = value[:1021] + "..."

            embed.add_field(
                name=f"{self.prefix}{shortcut.name}",
                value=value,
                inline=False
            )

        embed.set_footer(text=f"Page {page + 1} of {self.max_pages} • {len(self.shortcuts)} total shortcuts")
        return embed

    async def update_page(self, interaction: discord.Interaction):
        """Update the message with the current page"""
        embed = self.create_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)


class ShortcutBackButton(discord.ui.Button):
    """Button to go back to category selection"""

    def __init__(self, guild_id: int, settings_cache, cache):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="← Back to Categories",
            row=0
        )
        self.guild_id = guild_id
        self.settings_cache = settings_cache
        self.cache = cache

    async def callback(self, interaction: discord.Interaction):
        """Return to category selection"""
    
        shortcuts = await ShortcutEntry.get_by(guild_id=self.guild_id)
        categories = list(set(shortcut.category or "General" for shortcut in shortcuts))
        categories.sort()

        if not categories:
            embed = discord.Embed(
                title="No Shortcuts Found",
                description="This server has no shortcuts configured!",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return

        view = ShortcutListView(self.guild_id, self.settings_cache, self.cache)
        await view.setup_categories()
        embed = discord.Embed(
            title="Shortcut Categories",
            description="Select a category to view its shortcuts:",
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=view)


class ShortcutPreviousButton(discord.ui.Button):
    """Previous page button"""

    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="← Previous",
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle button click to go to the previous page"""
        view = self.view
        if view.current_page > 0:
            view.current_page -= 1
            await view.update_page(interaction)
        else:
            await interaction.response.defer()


class ShortcutNextButton(discord.ui.Button):
    """Next page button"""

    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Next →",
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle button click to go to the next page"""
        view = self.view
        if view.current_page < view.max_pages - 1:
            view.current_page += 1
            await view.update_page(interaction)
        else:
            await interaction.response.defer()


class ShortcutListView(discord.ui.View):
    """Main view for shortcut listing with category selection"""

    def __init__(self, guild_id: int, settings_cache, cache):
        super().__init__(timeout=300)  # 5 minute timeout
        self.guild_id = guild_id
        self.settings_cache = settings_cache
        self.cache = cache

    async def setup_categories(self):
        """Set up the category dropdown"""
        shortcuts = await ShortcutEntry.get_by(guild_id=self.guild_id)
        categories = list(set(shortcut.category or "General" for shortcut in shortcuts))
        categories.sort()

        if categories:
            select = ShortcutCategorySelect(categories, self.guild_id, self.settings_cache, self.cache)
            self.add_item(select)

class Shortcuts(Cog):
    """Adds simple text-shortcuts to the bot"""
    MAX_LEN = 20
    def __init__(self, bot):
        """cog init"""
        super().__init__(bot)
        self.settings_cache = db.ConfigCache(ShortcutSetting)
        self.cache = db.ConfigCache(ShortcutEntry)

    async def migrate_existing_shortcuts(self):
        """Migrate existing shortcuts to have default category if they don't have one"""
        try:
            async with db.Pool.acquire() as conn:
                await conn.execute("""
                    ALTER TABLE shortcuts
                    ADD COLUMN IF NOT EXISTS category varchar DEFAULT 'General'
                """)

                await conn.execute("""
                    UPDATE shortcuts
                    SET category = 'General'
                    WHERE category IS NULL
                """)

                await conn.execute("""
                    ALTER TABLE shortcut_settings
                    ADD COLUMN IF NOT EXISTS page_size integer DEFAULT 10
                """)
        except Exception as e:
            print(f"Migration error (this is normal for new installations): {e}")

    """Commands for managing shortcuts/macros."""
    @guild_only()
    @has_permissions(manage_messages=True)
    @group(invoke_without_command=True)
    async def shortcuts(self, ctx):
        """
        Display shortcut information
        """
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)

        if settings is None:
            raise BadArgument("This server has no shortcut configuration, set a prefix.")

        e = discord.Embed()
        e.title = "Server shortcut configuration"
        e.add_field(name="Shortcut prefix", value=settings.prefix or "[unset]")
        await ctx.send(embed=e)

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def setprefix(self, ctx, prefix):
        """Set the prefix to be used to respond to shortcuts for the server."""
        setting: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)

        if setting:
            setting.prefix = prefix
        else:
            setting = ShortcutSetting(guild_id=ctx.guild.id, prefix=prefix)

        await setting.update_or_add()
        self.settings_cache.invalidate_entry(guild_id=ctx.guild.id)

        await ctx.send(f"Set prefix to: {prefix}")

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def setpagesize(self, ctx, page_size: int):
        """Set the number of shortcuts to display per page (default: 10)."""
        if page_size < 1 or page_size > 25:
            raise BadArgument("Page size must be between 1 and 25.")

        setting: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)

        if setting:
            setting.page_size = page_size
        else:
            raise BadArgument("Set a prefix first!")

        await setting.update_or_add()
        self.settings_cache.invalidate_entry(guild_id=ctx.guild.id)

        await ctx.send(f"Set page size to: {page_size} shortcuts per page")

    setpagesize.example_usage = """
    `{prefix}shortcuts setpagesize 15` - shows 15 shortcuts per page instead of the default 10
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command(aliases=["add"])
    async def set(self, ctx, cmd_name, cmd_msg, *, category="General"):
        """Set the message to be sent for a given shortcut name with an optional category parameter."""
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)
        if settings is None:
            raise BadArgument("Set a prefix first!")
        if len(cmd_name) > self.MAX_LEN:
            raise BadArgument(f"command names can only be up to {self.MAX_LEN} chars long")
        if not cmd_msg:
            raise BadArgument("can't have null message")

        if len(category) > 50:
            raise BadArgument("Category names must be 50 characters or less.")

        if not category.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            raise BadArgument("Category names can only contain letters, numbers, spaces, hyphens, and underscores.")

        existing_shortcuts = await ShortcutEntry.get_by(guild_id=ctx.guild.id)
        existing_categories = set(shortcut.category or "General" for shortcut in existing_shortcuts)
        
        is_new_category = category not in existing_categories
        
        ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=cmd_name)

        if ent:
            ent.value = cmd_msg
            ent.category = category
        else:
            ent = ShortcutEntry(guild_id=ctx.guild.id, name=cmd_name, value=cmd_msg, category=category)

        await ent.update_or_add()
        self.cache.invalidate_entry(guild_id=ctx.guild.id, name=cmd_name)

        if is_new_category:
            await ctx.send(f"Created new category '{category}' and updated command successfully.")
        else:
            await ctx.send(f"Updated command successfully in category '{category}'.")

    set.example_usage = """
    `{prefix}shortcuts set hello "Hello, World!!!!"` - set !hello for the server in General category
    `{prefix}shortcuts set joke "Why did the chicken cross the road?" Fun` - set !joke in Fun category (creates Fun category if it doesn't exist)
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def remove(self, ctx, cmd_name):
        """Removes a shortcut from the server by name."""
        ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=cmd_name)

        if ent:
            await ShortcutEntry.delete(guild_id=ctx.guild.id, name=cmd_name)
            self.cache.invalidate_entry(guild_id=ctx.guild.id, name=cmd_name)
            await ctx.send(f"Removed command {cmd_name} successfully.")
        else:
            await ctx.send(f"No command named {cmd_name} found!")

    remove.example_usage = """
    `{prefix}shortcuts remove hello  - removes !hello
    """

    @guild_only()
    @shortcuts.command()
    async def list(self, ctx: DozerContext):
        """Lists all shortcuts for the server using an interactive menu (sent as a DM)."""
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)

        if settings is None:
            raise BadArgument("This server has no shortcut configuration, set a prefix.")

        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)

        if not ents:
            await ctx.send("No shortcuts for this server!")
            return

        view = ShortcutListView(ctx.guild.id, self.settings_cache, self.cache)
        await view.setup_categories()

        embed = discord.Embed(
            title="Shortcut Categories",
            description="Select a category to view its shortcuts:",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"{len(ents)} total shortcuts")

        try:
            await ctx.author.send(embed=embed, view=view)
            await ctx.send("I've sent you a DM with the shortcuts list!")
        except discord.Forbidden:
            await ctx.send("I couldn't send you a DM. Please check your privacy settings and try again.")

    list.example_usage = """
    `{prefix}shortcuts list` - sends you a DM with an interactive menu to browse shortcuts by category
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command(aliases=["movecategory", "changecategory", "setcategory"])
    async def move(self, ctx, cmd_name: str, category: str):
        """Move a shortcut to a different category."""

        if len(category) > 50:
            raise BadArgument("Category names must be 50 characters or less.")

        if not category.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            raise BadArgument("Category names can only contain letters, numbers, spaces, hyphens, and underscores.")

        ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=cmd_name)

        if not ent:
            all_ents = await ShortcutEntry.get_by(guild_id=ctx.guild.id)
            if all_ents:
                available = ", ".join([f"`{e.name}`" for e in all_ents[:10]])
                if len(all_ents) > 10:
                    available += f" and {len(all_ents) - 10} more..."
                await ctx.send(f"No shortcut named '{cmd_name}' found!\nAvailable shortcuts: {available}")
            else:
                await ctx.send(f"No shortcut named '{cmd_name}' found! This server has no shortcuts.")
            return

        old_category = ent.category or "General"

        if old_category.lower() == category.lower():
            await ctx.send(f"Shortcut '{cmd_name}' is already in category '{old_category}'.")
            return

        ent.category = category
        await ent.update_or_add()
        self.cache.invalidate_entry(guild_id=ctx.guild.id, name=cmd_name)

        embed = discord.Embed(
            title="✅ Shortcut Moved",
            description=f"Moved shortcut `{cmd_name}` from **{old_category}** to **{category}**",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    move.example_usage = """
    `{prefix}shortcuts setcategory hello Fun` - moves the 'hello' shortcut to the 'Fun' category
    `{prefix}shortcuts movecategory joke Humor` - moves the 'joke' shortcut to the 'Humor' category
    """

    @guild_only()
    @shortcuts.command()
    async def categories(self, ctx: DozerContext):
        """List all shortcut categories for this server."""
        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)

        if not ents:
            await ctx.send("No shortcuts for this server!")
            return

        category_counts = {}
        for ent in ents:
            category = ent.category or "General"
            category_counts[category] = category_counts.get(category, 0) + 1

        embed = discord.Embed(
            title="Shortcut Categories",
            color=discord.Color.blue()
        )

        for category, count in sorted(category_counts.items()):
            embed.add_field(
                name=category,
                value=f"{count} shortcut{'s' if count != 1 else ''}",
                inline=True
            )

        embed.set_footer(text=f"{len(ents)} total shortcuts")
        await ctx.send(embed=embed)

    categories.example_usage = """
    `{prefix}shortcuts categories` - lists all categories and shortcut counts
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def addcategory(self, ctx, category_name: str, *, description: str = None):
        """Create a new category. Categories are created automatically when shortcuts are added to them."""
        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)
        existing_categories = set(ent.category or "General" for ent in ents)

        if category_name in existing_categories:
            await ctx.send(f"Category '{category_name}' already exists!")
            return

    
        if len(category_name) > 50:
            raise BadArgument("Category names must be 50 characters or less.")

        if not category_name.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            raise BadArgument("Category names can only contain letters, numbers, spaces, hyphens, and underscores.")

        embed = discord.Embed(
            title="Category Ready",
            description=f"Category '{category_name}' is ready to use! Add shortcuts to it using:\n"
                       f"`{ctx.prefix}shortcuts set <shortcut_name> <message> {category_name}`",
            color=discord.Color.green()
        )
        if description:
            embed.add_field(name="Description", value=description, inline=False)

        await ctx.send(embed=embed)

    addcategory.example_usage = """
    `{prefix}shortcuts addcategory Fun` - prepares the 'Fun' category for use
    `{prefix}shortcuts addcategory Moderation Commands for moderators` - creates category with description
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def deletecategory(self, ctx, category_name: str, *, confirm: str = None):
        """Delete a category and optionally move its shortcuts to another category."""
        if category_name.lower() == "general":
            raise BadArgument("Cannot delete the 'General' category.")

        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id, category=category_name)

        if not ents:
            await ctx.send(f"Category '{category_name}' doesn't exist or has no shortcuts.")
            return

        if confirm != "CONFIRM":
            embed = discord.Embed(
                title="⚠️ Delete Category Confirmation",
                description=f"This will delete category '{category_name}' and move {len(ents)} shortcuts to 'General'.\n\n"
                           f"To confirm, run:\n`{ctx.prefix}shortcuts deletecategory {category_name} CONFIRM`",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Shortcuts that will be moved:",
                value=", ".join([f"`{ent.name}`" for ent in ents[:10]]) +
                      (f" and {len(ents) - 10} more..." if len(ents) > 10 else ""),
                inline=False
            )
            await ctx.send(embed=embed)
            return

        for ent in ents:
            ent.category = "General"
            await ent.update_or_add()
            self.cache.invalidate_entry(guild_id=ctx.guild.id, name=ent.name)

        embed = discord.Embed(
            title="✅ Category Deleted",
            description=f"Category '{category_name}' has been deleted.\n"
                       f"{len(ents)} shortcuts have been moved to 'General' category.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    deletecategory.example_usage = """
    `{prefix}shortcuts deletecategory Fun` - shows confirmation prompt
    `{prefix}shortcuts deletecategory Fun CONFIRM` - deletes the Fun category and moves shortcuts to General
    """

    async def category_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """
        Autocomplete for category names in slash commands.
        
        Args:
            interaction: The Discord interaction
            current: The current input string
            
        Returns:
            List of category choices that match the current input
        """
        try:
            ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)
            categories = list(set(ent.category or "General" for ent in ents))
            categories.sort()

            filtered = [cat for cat in categories if current.lower() in cat.lower()][:25]  # Discord limit

            return [app_commands.Choice(name=cat, value=cat) for cat in filtered]
        except Exception:
            return []

    async def shortcut_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """
        Autocomplete for shortcut names in slash commands.
        
        Args:
            interaction: The Discord interaction
            current: The current input string
            
        Returns:
            List of shortcut name choices that match the current input
        """
        try:
            ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)
            shortcut_names = [ent.name for ent in ents]

            filtered = [name for name in shortcut_names if current.lower() in name.lower()][:25]  # Discord limit

            return [app_commands.Choice(name=name, value=name) for name in filtered]
        except Exception:
            return []
    
    @guild_only()
    @shortcuts.command()
    async def csv(self, ctx):
        """Export all shortcuts for the server as a CSV."""
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)

        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)

        if not ents:
            await ctx.send("No shortcuts for this server!")
            return

        stringfile = io.StringIO()
        csvwriter = csv.writer(stringfile)
        
        csvwriter.writerow(["Shortcut", "Value", "Category"])
        for e in ents:
            csvwriter.writerow([settings.prefix + e.name, e.value, e.category or "General"])

        await ctx.send(file=discord.File(StringIO(stringfile.getvalue()),f"shortcuts-{ctx.guild.id}-{datetime.date.today().isoformat()}.csv"))

    csv.example_usage = """
        `{prefix}shortcuts csv - exports all shortcuts as a csv
        """


    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def bulk_delete(self, ctx, category: str = None, *, confirm: str = None):
        """Delete multiple shortcuts at once, optionally filtering by category."""
        if category and category.lower() == "all" and confirm != "CONFIRM":
            embed = discord.Embed(
                title="⚠️ Bulk Delete Confirmation",
                description="This will delete **ALL** shortcuts in this server.\n\n"
                           f"To confirm, run:\n`{ctx.prefix}shortcuts bulk_delete all CONFIRM`",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
            
        if category and category.lower() != "all" and confirm != "CONFIRM":
            embed = discord.Embed(
                title="⚠️ Bulk Delete Confirmation",
                description=f"This will delete **ALL** shortcuts in the '{category}' category.\n\n"
                           f"To confirm, run:\n`{ctx.prefix}shortcuts bulk_delete {category} CONFIRM`",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
            
        if not category and confirm != "CONFIRM":
            ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)
            if not ents:
                await ctx.send("No shortcuts for this server!")
                return
                
            categories = list(set(ent.category or "General" for ent in ents))
            categories.sort()
            
            embed = discord.Embed(
                title="Bulk Delete - Select Category",
                description="Please specify a category to delete shortcuts from, or use 'all' to delete all shortcuts.\n\n"
                           f"Example: `{ctx.prefix}shortcuts bulk_delete Fun CONFIRM`\n"
                           f"Or: `{ctx.prefix}shortcuts bulk_delete all CONFIRM`",
                color=discord.Color.blue()
            )
            
            categories_text = "\n".join([f"• {cat}" for cat in categories])
            embed.add_field(name="Available Categories", value=categories_text, inline=False)
            
            await ctx.send(embed=embed)
            return
            
        if category and category.lower() == "all" and confirm == "CONFIRM":
            count = await ShortcutEntry.delete_all(guild_id=ctx.guild.id)
            self.cache.invalidate_by_guild(ctx.guild.id)
            
            embed = discord.Embed(
                title="✅ Bulk Delete Complete",
                description=f"Deleted {count} shortcuts from all categories.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            return
            
        if category and confirm == "CONFIRM":
            ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id, category=category)
            
            if not ents:
                await ctx.send(f"No shortcuts found in category '{category}'.")
                return
                
            count = 0
            for ent in ents:
                await ShortcutEntry.delete(guild_id=ctx.guild.id, name=ent.name)
                self.cache.invalidate_entry(guild_id=ctx.guild.id, name=ent.name)
                count += 1
                
            embed = discord.Embed(
                title="✅ Bulk Delete Complete",
                description=f"Deleted {count} shortcuts from the '{category}' category.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

    bulk_delete.example_usage = """
    `{prefix}shortcuts bulk_delete` - Shows available categories to delete
    `{prefix}shortcuts bulk_delete Fun` - Shows confirmation to delete all shortcuts in Fun category
    `{prefix}shortcuts bulk_delete Fun CONFIRM` - Deletes all shortcuts in Fun category
    `{prefix}shortcuts bulk_delete all CONFIRM` - Deletes ALL shortcuts in the server
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def rename(self, ctx, old_name: str, new_name: str):
        """Rename a shortcut while keeping its value and category."""
        if len(new_name) > self.MAX_LEN:
            raise BadArgument(f"Shortcut names can only be up to {self.MAX_LEN} characters long")
            
        old_ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=old_name)
        if not old_ent:
            await ctx.send(f"No shortcut named '{old_name}' found!")
            return
            
        new_ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=new_name)
        if new_ent:
            await ctx.send(f"A shortcut named '{new_name}' already exists!")
            return
            
        new_shortcut = ShortcutEntry(
            guild_id=ctx.guild.id,
            name=new_name,
            value=old_ent.value,
            category=old_ent.category
        )
        
        await new_shortcut.update_or_add()
        await ShortcutEntry.delete(guild_id=ctx.guild.id, name=old_name)
        
        self.cache.invalidate_entry(guild_id=ctx.guild.id, name=old_name)
        self.cache.invalidate_entry(guild_id=ctx.guild.id, name=new_name)
        
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)
        prefix = settings.prefix if settings else ""
        
        embed = discord.Embed(
            title="✅ Shortcut Renamed",
            description=f"Renamed shortcut from `{prefix}{old_name}` to `{prefix}{new_name}`",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    rename.example_usage = """
    `{prefix}shortcuts rename hello greeting` - renames the shortcut 'hello' to 'greeting'
    """


    @Cog.listener()
    async def on_message(self, msg):
        """prefix scanner"""
        if not msg.guild or msg.author.bot:
            return
        setting = await self.settings_cache.query_one(guild_id=msg.guild.id)
        if setting is None:
            return

        c = msg.content
        if len(c) < len(setting.prefix):
            return

        if not c.startswith(setting.prefix):
            return

        shortcuts = await ShortcutEntry.get_by(guild_id=msg.guild.id)
        if not shortcuts:
            return

        command_name = c.lower()[len(setting.prefix):]
        
        for shortcut in shortcuts:
            if command_name == shortcut.name.lower():
                await msg.channel.send(shortcut.value)
                return

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command()
    async def import_shortcuts(self, ctx: DozerContext):
        """Import shortcuts from a CSV file (attached to the message).
        CSV format should have columns: Shortcut, Value, Category"""
        if not ctx.message.attachments:
            await ctx.send("Please attach a CSV file to import shortcuts from.")
            return
            
        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith('.csv'):
            await ctx.send("Please attach a CSV file (must have .csv extension).")
            return
            
        try:
            # Read and parse CSV
            csv_content = (await attachment.read()).decode('utf-8')
            import_results = await self._process_csv_import(ctx, csv_content)
            
            # Send results
            embed = discord.Embed(
                title="CSV Import Results",
                color=discord.Color.green() if import_results["imported"] > 0 else discord.Color.red()
            )
            
            embed.add_field(name="Imported", value=str(import_results["imported"]), inline=True)
            embed.add_field(name="Skipped", value=str(import_results["skipped"]), inline=True)
            
            if import_results["errors"]:
                error_text = "\n".join(import_results["errors"][:10])
                if len(import_results["errors"]) > 10:
                    error_text += f"\n... and {len(import_results['errors']) - 10} more errors"
                embed.add_field(name="Errors", value=error_text, inline=False)
                
            await ctx.send(embed=embed)
            
        except UnicodeDecodeError:
            await ctx.send("Could not decode the CSV file. Please ensure it's saved with UTF-8 encoding.")
        except csv.Error:
            await ctx.send("Invalid CSV format. Please check your file and try again.")
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    async def _process_csv_import(self, ctx, csv_content):
        """Process CSV content and import shortcuts
        
        Args:
            ctx: Command context
            csv_content: String content of the CSV file
            
        Returns:
            Dictionary with import results
        """
        reader = csv.reader(io.StringIO(csv_content))
        first_row = next(reader, None)
        
        if not first_row or len(first_row) < 2:
            raise ValueError("Invalid CSV format. File must have at least 'Shortcut' and 'Value' columns.")
        
        # Determine column positions
        column_info = self._get_csv_column_info(first_row)
        has_headers = column_info["has_headers"]
        
        # Get settings for prefix
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)
        if settings is None:
            raise ValueError(f"Set a prefix first using `{ctx.prefix}shortcuts setprefix <prefix>`")
            
        # Process rows
        results = {"imported": 0, "skipped": 0, "errors": []}
        
        # If first row wasn't headers, process it as data
        if not has_headers:
            # Reset reader to include first row
            reader = csv.reader(io.StringIO(csv_content))
        
        for row_num, row in enumerate(reader, 1 if has_headers else 0):
            try:
                await self._process_csv_row(row, row_num, column_info, settings, results, ctx.guild.id)
            except Exception as e:
                results["errors"].append(f"Row {row_num + 1}: Error - {str(e)}")
                results["skipped"] += 1
                
        return results
        
    def _get_csv_column_info(self, first_row):
        """Determine CSV column positions and if headers are present
        
        Args:
            first_row: First row of the CSV
            
        Returns:
            Dictionary with column information
        """
        has_headers = False
        shortcut_col = 0
        value_col = 1
        category_col = 2 if len(first_row) > 2 else None
        
        # Check if first row looks like headers
        if first_row[0].lower() == 'shortcut' and first_row[1].lower() == 'value':
            has_headers = True
            # Find column indices from headers
            for i, header in enumerate(first_row):
                header_lower = header.lower()
                if header_lower == 'shortcut':
                    shortcut_col = i
                elif header_lower == 'value':
                    value_col = i
                elif header_lower == 'category':
                    category_col = i
                    
        return {
            "has_headers": has_headers,
            "shortcut_col": shortcut_col,
            "value_col": value_col,
            "category_col": category_col
        }
        
    async def _process_csv_row(self, row, row_num, column_info, settings, results, guild_id):
        """Process a single CSV row
        
        Args:
            row: CSV row data
            row_num: Row number (for error reporting)
            column_info: Column position information
            settings: Guild shortcut settings
            results: Results dictionary to update
            guild_id: Guild ID
            
        Returns:
            None (updates results dictionary)
        """
        shortcut_col = column_info["shortcut_col"]
        value_col = column_info["value_col"]
        category_col = column_info["category_col"]
        
        if len(row) <= max(shortcut_col, value_col):
            results["errors"].append(f"Row {row_num + 1}: Not enough columns")
            return
            
        shortcut_name = row[shortcut_col]
        value = row[value_col]
        category = row[category_col] if category_col is not None and len(row) > category_col else "General"
        
        # Remove prefix if it exists
        if shortcut_name.startswith(settings.prefix):
            shortcut_name = shortcut_name[len(settings.prefix):]
            
        # Validate shortcut name
        if len(shortcut_name) > self.MAX_LEN:
            results["errors"].append(f"Row {row_num + 1}: Shortcut name too long (max {self.MAX_LEN} chars)")
            results["skipped"] += 1
            return
            
        if not shortcut_name or not value:
            results["errors"].append(f"Row {row_num + 1}: Shortcut name or value is empty")
            results["skipped"] += 1
            return
            
        # Validate category
        if len(category) > 50:
            results["errors"].append(f"Row {row_num + 1}: Category name too long (max 50 chars)")
            category = "General"  # Default to General if too long
            
        if not category.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            results["errors"].append(f"Row {row_num + 1}: Invalid category name (only letters, numbers, spaces, hyphens, underscores)")
            category = "General"  # Default to General if invalid
        
        # Create or update shortcut
        ent: ShortcutEntry = await self.cache.query_one(guild_id=guild_id, name=shortcut_name)
        
        if ent:
            ent.value = value
            ent.category = category
        else:
            ent = ShortcutEntry(guild_id=guild_id, name=shortcut_name, value=value, category=category)
            
        await ent.update_or_add()
        self.cache.invalidate_entry(guild_id=guild_id, name=shortcut_name)
        results["imported"] += 1

    import_shortcuts.example_usage = """
    `{prefix}import_shortcuts` - Upload a CSV file with your shortcuts when using this command
    
    CSV format examples:
    
    With headers:
    Shortcut,Value,Category
    hello,Hello World!,Greetings
    rules,Please follow our server rules,Moderation
    
    Without headers (assumes first column is shortcut, second is value, third is category):
    hello,Hello World!,Greetings
    rules,Please follow our server rules,Moderation
    """

async def setup(bot):
    """Adds the shortcuts cog to the main bot project."""
    cog = Shortcuts(bot)
    await cog.migrate_existing_shortcuts()
    await bot.add_cog(cog)


"""Database Tables"""


class ShortcutSetting(db.DatabaseTable):
    """Provides a DB config to track shortcut setting per guild."""
    __tablename__ = 'shortcut_settings'
    __uniques__ = "guild_id"

    @classmethod
    async def initial_create(cls):
        """Create the table in the database"""
        async with db.Pool.acquire() as conn:
            await conn.execute(f"""
            CREATE TABLE {cls.__tablename__} (
            guild_id bigint PRIMARY KEY NOT NULL,
            prefix varchar NOT NULL,
            page_size integer DEFAULT 10
            )""")

    def __init__(self, guild_id: int, prefix: str, page_size: int = 10):
        super().__init__()
        self.guild_id = guild_id
        self.prefix = prefix
        self.page_size = page_size

    @classmethod
    async def get_by(cls, **kwargs):
        results = await super().get_by(**kwargs)
        result_list = []
        for result in results:
            obj = ShortcutSetting(guild_id=result.get("guild_id"),
                                prefix=result.get("prefix"),
                                page_size=result.get("page_size", 10))
            result_list.append(obj)
        return result_list

class ShortcutEntry(db.DatabaseTable):
    """Provides a DB config to track shortcut entries."""
    __tablename__ = 'shortcuts'
    __uniques__ = 'guild_id, name'

    @classmethod
    async def initial_create(cls):
        """Create the table in the database"""
        async with db.Pool.acquire() as conn:
            await conn.execute(f"""
            CREATE TABLE {cls.__tablename__} (
            guild_id bigint NOT NULL,
            name varchar NOT NULL,
            value text NOT NULL,
            category varchar DEFAULT 'General',
            PRIMARY KEY (guild_id, name)
            )""")

    def __init__(self, guild_id: int, name: str, value: str, category: str = "General"):
        super().__init__()
        self.guild_id = guild_id
        self.name = name
        self.value = value
        self.category = category

    @classmethod
    async def get_by(cls, **kwargs):
        results = await super().get_by(**kwargs)
        result_list = []
        for result in results:
            obj = ShortcutEntry(guild_id=result.get("guild_id"),
                                name=result.get("name"),
                                value=result.get("value"),
                                category=result.get("category", "General"))
            result_list.append(obj)
        return result_list
        
