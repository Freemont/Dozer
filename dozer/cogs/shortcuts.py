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

        # Get shortcuts for the selected category
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

        # Create paginated view with configured page size
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

        # Add back button to return to category selection
        self.add_item(ShortcutBackButton(guild_id, settings_cache, cache))

        # Add pagination buttons if needed
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
            # Truncate long values for display
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
        # Get all categories
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

        # Create new category selection view
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
                # Add category column if it doesn't exist
                await conn.execute("""
                    ALTER TABLE shortcuts
                    ADD COLUMN IF NOT EXISTS category varchar DEFAULT 'General'
                """)

                # Update existing shortcuts without categories
                await conn.execute("""
                    UPDATE shortcuts
                    SET category = 'General'
                    WHERE category IS NULL
                """)

                # Add page_size column to settings if it doesn't exist
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
    async def set(self, ctx, cmd_name, *, cmd_msg):
        """Set the message to be sent for a given shortcut name. Optionally specify category with --category."""
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)
        if settings is None:
            raise BadArgument("Set a prefix first!")
        if len(cmd_name) > self.MAX_LEN:
            raise BadArgument(f"command names can only be up to {self.MAX_LEN} chars long")
        if not cmd_msg:
            raise BadArgument("can't have null message")

        # Parse category from message if specified
        category = "General"
        if "--category" in cmd_msg:
            parts = cmd_msg.split("--category", 1)
            if len(parts) == 2:
                cmd_msg = parts[0].strip()
                category_part = parts[1].strip()
                if category_part:
                    category = category_part.split()[0]  # Take first word after --category

        ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=cmd_name)

        if ent:
            ent.value = cmd_msg
            ent.category = category
        else:
            ent = ShortcutEntry(guild_id=ctx.guild.id, name=cmd_name, value=cmd_msg, category=category)

        await ent.update_or_add()
        self.cache.invalidate_entry(guild_id=ctx.guild.id, name=cmd_name)

        await ctx.send(f"Updated command successfully in category '{category}'.")

    set.example_usage = """
    `{prefix}shortcuts set hello Hello, World!!!!` - set !hello for the server in General category
    `{prefix}shortcuts set joke Why did the chicken cross the road? --category Fun` - set !joke in Fun category
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
        """Lists all shortcuts for the server using an interactive menu."""
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=ctx.guild.id)

        if settings is None:
            raise BadArgument("This server has no shortcut configuration, set a prefix.")

        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)

        if not ents:
            await ctx.send("No shortcuts for this server!")
            return

        # Create the interactive view
        view = ShortcutListView(ctx.guild.id, self.settings_cache, self.cache)
        await view.setup_categories()

        embed = discord.Embed(
            title="Shortcut Categories",
            description="Select a category to view its shortcuts:",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"{len(ents)} total shortcuts")

        await ctx.send(embed=embed, view=view)

    list.example_usage = """
    `{prefix}shortcuts list - shows interactive menu to browse shortcuts by category
    """

    @guild_only()
    @has_permissions(manage_messages=True)
    @shortcuts.command(aliases=["movecategory", "changecategory"])
    async def setcategory(self, ctx, cmd_name: str, category: str):
        """Move a shortcut to a different category."""
        # Validate category name
        if len(category) > 50:
            raise BadArgument("Category names must be 50 characters or less.")

        if not category.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            raise BadArgument("Category names can only contain letters, numbers, spaces, hyphens, and underscores.")

        ent: ShortcutEntry = await self.cache.query_one(guild_id=ctx.guild.id, name=cmd_name)

        if not ent:
            # Show available shortcuts
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

    setcategory.example_usage = """
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

        # Get unique categories and count shortcuts in each
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
        # Check if category already exists
        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=ctx.guild.id)
        existing_categories = set(ent.category or "General" for ent in ents)

        if category_name in existing_categories:
            await ctx.send(f"Category '{category_name}' already exists!")
            return

        # Categories are created implicitly when shortcuts are added to them
        # For now, we'll just confirm the category name is valid
        if len(category_name) > 50:
            raise BadArgument("Category names must be 50 characters or less.")

        if not category_name.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            raise BadArgument("Category names can only contain letters, numbers, spaces, hyphens, and underscores.")

        embed = discord.Embed(
            title="Category Ready",
            description=f"Category '{category_name}' is ready to use! Add shortcuts to it using:\n"
                       f"`{ctx.prefix}shortcuts set <shortcut_name> <message> --category {category_name}`",
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

        # Get all shortcuts in this category
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

        # Move all shortcuts to General category
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

    # Slash Commands for Category Management
    @app_commands.command(name="shortcut-add-category", description="Create a new shortcut category")
    @app_commands.describe(
        category_name="Name of the category to create",
        description="Optional description for the category"
    )
    @app_commands.guild_only()
    async def slash_addcategory(self, interaction: discord.Interaction, category_name: str, description: str = None):
        """Slash command to create a new category."""
        # Check permissions
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need 'Manage Messages' permission to use this command.", 
                                                       ephemeral=True)
            return

        # Check if category already exists
        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)
        existing_categories = set(ent.category or "General" for ent in ents)

        if category_name in existing_categories:
            await interaction.response.send_message(f"❌ Category '{category_name}' already exists!", ephemeral=True)
            return

        # Validate category name
        if len(category_name) > 50:
            await interaction.response.send_message("❌ Category names must be 50 characters or less.", ephemeral=True)
            return

        if not category_name.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            await interaction.response.send_message(
                "❌ Category names can only contain letters, numbers, spaces, hyphens, and underscores.", 
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="✅ Category Ready",
            description=f"Category '{category_name}' is ready to use! Add shortcuts to it using the `/shortcut-set` command.",
            color=discord.Color.green()
        )
        if description:
            embed.add_field(name="Description", value=description, inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shortcut-delete-category", description="Delete a shortcut category")
    @app_commands.describe(
        category_name="Name of the category to delete",
        confirm="Type 'CONFIRM' to confirm deletion"
    )
    @app_commands.guild_only()
    async def slash_deletecategory(self, interaction: discord.Interaction, category_name: str, confirm: str = None):
        """Slash command to delete a category."""
        # Check permissions
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need 'Manage Messages' permission to use this command.", 
                                                       ephemeral=True)
            return

        if category_name.lower() == "general":
            await interaction.response.send_message("❌ Cannot delete the 'General' category.", ephemeral=True)
            return

        # Get all shortcuts in this category
        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id, category=category_name)

        if not ents:
            await interaction.response.send_message(f"❌ Category '{category_name}' doesn't exist or has no shortcuts.", 
                                                       ephemeral=True)
            return

        if confirm != "CONFIRM":
            embed = discord.Embed(
                title="⚠️ Delete Category Confirmation",
                description=f"This will delete category '{category_name}' and move {len(ents)} shortcuts to 'General'.\n\n"
                           f"To confirm, use this command again with `confirm: CONFIRM`",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Shortcuts that will be moved:",
                value=", ".join([f"`{ent.name}`" for ent in ents[:10]]) +
                      (f" and {len(ents) - 10} more..." if len(ents) > 10 else ""),
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Move all shortcuts to General category
        for ent in ents:
            ent.category = "General"
            await ent.update_or_add()
            self.cache.invalidate_entry(guild_id=interaction.guild.id, name=ent.name)

        embed = discord.Embed(
            title="✅ Category Deleted",
            description=f"Category '{category_name}' has been deleted.\n"
                       f"{len(ents)} shortcuts have been moved to 'General' category.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shortcut-move-category", description="Move a shortcut to a different category")
    @app_commands.describe(
        shortcut_name="Name of the shortcut to move",
        category="Category to move the shortcut to"
    )
    @app_commands.guild_only()
    async def slash_setcategory(self, interaction: discord.Interaction, shortcut_name: str, category: str):
        """Slash command to move a shortcut to a different category."""
        # Check permissions
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need 'Manage Messages' permission to use this command.", 
                                                       ephemeral=True)
            return

        # Validate category name
        if len(category) > 50:
            await interaction.response.send_message("❌ Category names must be 50 characters or less.", 
                                                       ephemeral=True)
            return

        if not category.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            await interaction.response.send_message("❌ Category names can only contain letters, numbers, spaces, hyphens, and underscores.", 
                                                       ephemeral=True)
            return

        ent: ShortcutEntry = await self.cache.query_one(guild_id=interaction.guild.id, name=shortcut_name)

        if not ent:
            # Show available shortcuts
            all_ents = await ShortcutEntry.get_by(guild_id=interaction.guild.id)
            if all_ents:
                available = ", ".join([f"`{e.name}`" for e in all_ents[:10]])
                if len(all_ents) > 10:
                    available += f" and {len(all_ents) - 10} more..."
                await interaction.response.send_message(f"❌ No shortcut named '{shortcut_name}' found!\nAvailable shortcuts: {available}", 
                                                           ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ No shortcut named '{shortcut_name}' found! This server has no shortcuts.", 
                                                           ephemeral=True)
            return

        old_category = ent.category or "General"

        if old_category.lower() == category.lower():
            await interaction.response.send_message(f"❌ Shortcut '{shortcut_name}' is already in category '{old_category}'.", 
                                                       ephemeral=True)
            return

        ent.category = category
        await ent.update_or_add()
        self.cache.invalidate_entry(guild_id=interaction.guild.id, name=shortcut_name)

        embed = discord.Embed(
            title="✅ Shortcut Moved",
            description=f"Moved shortcut `{shortcut_name}` from **{old_category}** to **{category}**",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shortcut-list-categories", description="List all shortcut categories")
    @app_commands.guild_only()
    async def slash_categories(self, interaction: discord.Interaction):
        """Slash command to list all categories."""
        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)

        if not ents:
            await interaction.response.send_message("❌ This server has no shortcuts!", ephemeral=True)
            return

        # Get unique categories and count shortcuts in each
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
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shortcut-list", description="Browse shortcuts by category")
    @app_commands.guild_only()
    async def slash_list(self, interaction: discord.Interaction):
        """Slash command to list shortcuts with interactive menu."""
        settings: ShortcutSetting = await self.settings_cache.query_one(guild_id=interaction.guild.id)

        if settings is None:
            await interaction.response.send_message("❌ This server has no shortcut configuration. An admin needs to set a prefix first.", 
                                                       ephemeral=True)
            return

        ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)

        if not ents:
            await interaction.response.send_message("❌ This server has no shortcuts!", ephemeral=True)
            return

        # Create the interactive view
        view = ShortcutListView(interaction.guild.id, self.settings_cache, self.cache)
        await view.setup_categories()

        embed = discord.Embed(
            title="Shortcut Categories",
            description="Select a category to view its shortcuts:",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"{len(ents)} total shortcuts")

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # Autocomplete functions for slash commands
    async def category_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for category names"""
        try:
            ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)
            categories = list(set(ent.category or "General" for ent in ents))
            categories.sort()

            # Filter categories based on current input
            filtered = [cat for cat in categories if current.lower() in cat.lower()][:25]  # Discord limit

            return [app_commands.Choice(name=cat, value=cat) for cat in filtered]
        except Exception:
            return []

    async def shortcut_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for shortcut names"""
        try:
            ents: List[ShortcutEntry] = await ShortcutEntry.get_by(guild_id=interaction.guild.id)
            shortcut_names = [ent.name for ent in ents]

            # Filter shortcuts based on current input
            filtered = [name for name in shortcut_names if current.lower() in name.lower()][:25]  # Discord limit

            return [app_commands.Choice(name=name, value=name) for name in filtered]
        except Exception:
            return []

    # Add autocomplete to the slash commands
    slash_deletecategory.autocomplete('category_name')(category_autocomplete)
    slash_setcategory.autocomplete('shortcut_name')(shortcut_autocomplete)
    slash_setcategory.autocomplete('category')(category_autocomplete)

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
        # Add header row
        csvwriter.writerow(["Shortcut", "Value", "Category"])
        for e in ents:
            csvwriter.writerow([settings.prefix + e.name, e.value, e.category or "General"])

        await ctx.send(file=discord.File(StringIO(stringfile.getvalue()),f"shortcuts-{ctx.guild.id}-{datetime.date.today().isoformat()}.csv"))

    csv.example_usage = """
        `{prefix}shortcuts csv - exports all shortcuts as a csv
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

        for shortcut in shortcuts:
            if c.lower()[len(setting.prefix):] == shortcut.name.lower():
                await msg.channel.send(shortcut.value)
                return

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
        
