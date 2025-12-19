"""Slack onboarding and subscription handlers."""

import logging
from slack_bolt.async_app import AsyncApp
from slack_bolt.request.async_request import AsyncBoltRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import async_session
from app.services.workspace_service import get_or_create_workspace
from app.services.user_service import create_user, list_users_by_workspace
from app.schemas.user import UserCreate

logger = logging.getLogger(__name__)


async def register_onboarding_handlers(app: AsyncApp) -> None:
    """Register onboarding and subscription handlers.

    Args:
        app: AsyncApp instance
    """

    @app.event("app_mention")
    async def handle_app_mention(body: dict, say, client, logger):
        """Handle app mentions - show help/welcome message.

        Args:
            body: Slack event payload
            say: Function to send messages
            client: Slack client
            logger: Logging function
        """
        user_id = body["event"]["user"]
        logger.info(f"App mentioned by {user_id}")

        await say(
            text=":wave: Hi! I'm the Daily Standup Bot.\n\n"
            "Use `/standup subscribe` to start receiving daily standup prompts!\n"
            "Use `/standup unsubscribe` to stop receiving them.\n"
            "Use `/standup status` to see who's subscribed."
        )

    @app.command("/standup")
    async def handle_standup_command(ack, body, respond, client):
        """Handle /standup slash command.

        Args:
            ack: Acknowledge the command
            body: Slack command payload
            respond: Function to respond to command
            client: Slack client
        """
        await ack()

        subcommand = body.get("text", "").strip().lower()
        user_id = body["user_id"]
        team_id = body["team_id"]
        channel_id = body["channel_id"]

        async with async_session() as session:
            try:
                # Get or create workspace
                workspace = await get_or_create_workspace(
                    session, team_id, channel_id
                )
                workspace_id = workspace["workspace_id"]

                # Get user info from Slack
                user_info = await client.users_info(user=user_id)
                slack_user = user_info["user"]

                display_name = slack_user.get("real_name") or slack_user.get("name")
                email = slack_user.get("profile", {}).get("email")

                if subcommand == "subscribe":
                    user_create = UserCreate(
                        slack_user_id=user_id,
                        display_name=display_name,
                        email=email,
                        timezone=None,  # Can be set later
                    )
                    result = await create_user(session, workspace_id, user_create)

                    if "error" in result:
                        await respond(
                            f":warning: You're already subscribed to daily standups!"
                        )
                    else:
                        await respond(
                            f":tada: Welcome! You're now subscribed to daily standups. "
                            f"You'll receive prompts at {workspace['default_time']} "
                            f"({workspace['timezone']})."
                        )
                        logger.info(f"User subscribed: {user_id}")

                elif subcommand == "unsubscribe":
                    from app.db.repository import UserRepository

                    repo = UserRepository(session)
                    user = await repo.get_by_slack_id_and_workspace(
                        user_id, workspace_id
                    )

                    if user:
                        await repo.update(user.id, active=False)
                        await session.commit()
                        await respond(
                            ":wave: You've been unsubscribed from daily standups."
                        )
                        logger.info(f"User unsubscribed: {user_id}")
                    else:
                        await respond(
                            ":info: You're not currently subscribed to standups."
                        )

                elif subcommand == "status":
                    users = await list_users_by_workspace(session, workspace_id)
                    user_count = len(users)
                    user_list = "\n".join(
                        f"• {u['display_name']}" for u in users
                    )

                    message = (
                        f":clipboard: *Daily Standup Status*\n"
                        f"Subscribed users: {user_count}\n\n"
                    )

                    if user_list:
                        message += f"*Subscribers:*\n{user_list}"
                    else:
                        message += "No subscribers yet. Use `/standup subscribe` to join!"

                    await respond(message)

                elif subcommand == "" or subcommand == "help":
                    # Show help if no subcommand
                    await respond(
                        "*Daily Standup Bot Commands*\n\n"
                        "`/standup subscribe` - Subscribe to daily standups\n"
                        "`/standup unsubscribe` - Stop receiving standups\n"
                        "`/standup status` - See subscriber count\n"
                        "`/standup help` - Show this message"
                    )
                else:
                    await respond(
                        f":x: Unknown command. Use `/standup help` for available commands."
                    )

            except Exception as e:
                logger.error(f"Error handling standup command: {e}", exc_info=True)
                await respond(
                    f":x: An error occurred. Please try again later. (Error: {str(e)})"
                )


async def register_installation_handler(app: AsyncApp) -> None:
    """Register app installation handler to initialize workspace.

    Args:
        app: AsyncApp instance
    """

    @app.event("app_installed")
    async def handle_app_installed(body, logger, client):
        """Handle app installation event.

        Args:
            body: Slack event payload
            logger: Logging function
            client: Slack client
        """
        team_id = body.get("team", {}).get("id")

        if not team_id:
            logger.warning("app_installed event missing team ID")
            return

        logger.info(f"Bot installed to workspace: {team_id}")

        try:
            async with async_session() as session:
                # Get default channel (usually #general or #announcements)
                channels = client.conversations_list(
                    types="public_channel", exclude_archived=True
                )

                report_channel_id = None
                for channel in channels.get("channels", []):
                    if channel["name"] in ["general", "announcements", "standup"]:
                        report_channel_id = channel["id"]
                        break

                if not report_channel_id and channels.get("channels"):
                    report_channel_id = channels["channels"][0]["id"]

                # Create workspace
                workspace = await get_or_create_workspace(
                    session, team_id, report_channel_id or "general"
                )
                logger.info(f"Workspace created: {workspace['workspace_id']}")

                # Send welcome message to a channel
                if report_channel_id:
                    client.chat_postMessage(
                        channel=report_channel_id,
                        text=":wave: *Daily Standup Bot Installed!*\n\n"
                        "Use `/standup subscribe` to start receiving daily standup prompts.\n"
                        "Type `/standup help` for more commands.",
                    )
        except Exception as e:
            logger.error(f"Error initializing workspace after installation: {e}")
