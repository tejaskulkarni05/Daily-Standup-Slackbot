"""Slack event handlers for messages and interactions."""

import logging
from typing import Optional
from datetime import date
from slack_bolt.async_app import AsyncApp
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_bolt.response import BoltResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.slack.messages import (
    build_completion_message,
    build_error_message,
    build_skip_notification_message,
    build_question_message,
)
from app.db.repository import UserRepository, StandupReportRepository, StandupStateRepository
from app.services.standup_service import handle_user_answer, handle_skip_today, post_report_to_channel
from app.utils.slack_utils import is_bot_message
from app.utils.timeutils import get_user_date
from app.db.base import async_session

logger = logging.getLogger(__name__)


async def register_handlers(app: AsyncApp) -> None:
    """Register all Slack event handlers with the Bolt app.

    Args:
        app: AsyncApp instance
    """

    @app.event("message")
    async def handle_message(body: dict, say, client, logger):
        """Handle direct messages from users - process standup answers.

        Args:
            body: Slack event payload
            say: Function to send messages
            client: Slack client
            logger: Logging function
        """
        # Ignore bot messages
        if is_bot_message(body.get("event", {})):
            return

        message = body.get("event", {})
        user_id = message.get("user")
        text = message.get("text", "").strip()
        channel = message.get("channel", "")

        if not user_id or not text:
            return

        logger.info(f"Received message from {user_id}: {text[:50]}")

        try:
            async with async_session() as session:
                # Check if user has a pending standup
                user_repo = UserRepository(session)
                state_repo = StandupStateRepository(session)

                user = await user_repo.get_by_slack_id(user_id)
                if not user:
                    # User not subscribed - offer to subscribe
                    await say(
                        text=":wave: Hi! I'm the Daily Standup Bot.\n\n"
                        "You're not currently subscribed to standups. "
                        "Use `/standup subscribe` to start receiving daily prompts!"
                    )
                    return

                state = await state_repo.get_by_user(user.id)
                if not state:
                    # No pending standup - offer to start one manually or wait
                    await say(
                        text="No pending standup right now. "
                        "I'll send you a standup prompt at the scheduled time. "
                        # "If you want to submit a standup now, use `/standup start`."
                    )
                    return

                # Process the answer
                result = await handle_user_answer(session, user_id, text)

                if result.get("action") == "error":
                    await say(**build_error_message(result.get("message", "An error occurred")))
                    return

                # Update the previous message to remove buttons after answer is submitted
                try:
                    # Get conversation history to find the last bot message
                    history = await client.conversations_history(channel=channel, limit=10)
                    if history.get("messages"):
                        # Find the most recent bot message (second from last, since the current message is the user's answer)
                        for msg in history.get("messages", []):
                            if msg.get("bot_id") or msg.get("app_id"):
                                # Found a bot message, update it to remove buttons
                                prev_blocks = msg.get("blocks", [])
                                # Remove action blocks (buttons)
                                updated_blocks = [block for block in prev_blocks if block.get("type") != "actions"]
                                
                                if updated_blocks and len(updated_blocks) < len(prev_blocks):
                                    # Only update if we actually removed buttons
                                    await client.chat_update(
                                        channel=channel,
                                        ts=msg.get("ts"),
                                        blocks=updated_blocks,
                                    )
                                    logger.info(f"Updated previous message for user {user_id} to remove buttons")
                                break
                except Exception as e:
                    logger.warning(f"Could not update previous message: {e}")

                if result.get("action") == "next_question":
                    # Ask next question
                    question_index = result.get("question_index", 0)
                    previous_report_today = None
                    
                    # For question 1 (yesterday), fetch previous report's today answer
                    if question_index == 1:
                        report_repo = StandupReportRepository(session)
                        latest_report = await report_repo.get_latest_by_user(user.id)
                        if latest_report and latest_report.today:
                            previous_report_today = latest_report.today
                    
                    msg = build_question_message(question_index, "", previous_report_today=previous_report_today)
                    await say(**msg)
                    logger.info(f"Sent question {question_index} to user {user_id}")

                elif result.get("action") == "complete_report":
                    # All questions answered
                    await say(**build_completion_message())
                    logger.info(f"Standup completed for user {user_id}")
                    
                    # Post report to channel
                    report_id = result.get("report_id")
                    if report_id:
                        try:
                            post_result = await post_report_to_channel(session, report_id)
                            if post_result.get("action") == "posted":
                                logger.info(f"Report {report_id} posted to channel")
                            else:
                                logger.warning(f"Failed to post report: {post_result.get('message')}")
                        except Exception as e:
                            logger.error(f"Error posting report to channel: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error handling message from {user_id}: {e}", exc_info=True)
            await say(**build_error_message(f"An error occurred: {str(e)}"))

    @app.action("button_skip_today")
    async def handle_skip_button(ack, body, say, client, logger):
        """Handle 'Skip Today' button click.

        Args:
            ack: Acknowledge the action
            body: Slack action payload
            say: Function to send messages
            client: Slack client
            logger: Logging function
        """
        await ack()

        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        timestamp = body["message"]["ts"]
        logger.info(f"User {user_id} clicked 'Skip Today'")

        try:
            # Get user info from Slack
            user_info = await client.users_info(user=user_id)
            display_name = user_info["user"].get("real_name") or user_info["user"].get("name")

            async with async_session() as session:
                result = await handle_skip_today(session, user_id)

                if result.get("action") == "error":
                    # Update original message to remove buttons and show error
                    await client.chat_update(
                        channel=channel,
                        ts=timestamp,
                        text="Standup Error",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f":warning: *Oops!*\n{result.get('message', 'Error skipping standup')}",
                                },
                            },
                        ],
                    )
                else:
                    # Update original message to remove buttons and show confirmation
                    await client.chat_update(
                        channel=channel,
                        ts=timestamp,
                        text="Skipped",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": ":ok_hand: You've skipped today's standup.",
                                },
                            }
                        ],
                    )
                    logger.info(f"Skipped standup for user {user_id}")

        except Exception as e:
            logger.error(f"Error handling skip: {e}")
            # Try to update the message with error
            try:
                await client.chat_update(
                    channel=channel,
                    ts=timestamp,
                    text="Error",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":warning: Error: {str(e)}",
                            },
                        },
                    ],
                )
            except Exception as update_error:
                logger.error(f"Error updating message: {update_error}")
                await say(**build_error_message(f"Error: {str(e)}"))

    @app.action("button_pause_standups")
    async def handle_pause_button(ack, body, say, client, logger):
        """Handle 'Pause Standups' button click.

        Args:
            ack: Acknowledge the action
            body: Slack action payload
            say: Function to send messages
            client: Slack client
            logger: Logging function
        """
        await ack()

        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        timestamp = body["message"]["ts"]
        logger.info(f"User {user_id} clicked 'Pause Standups'")

        try:
            async with async_session() as session:
                from app.db.repository import UserRepository

                user_repo = UserRepository(session)
                user = await user_repo.get_by_slack_id(user_id)

                if not user:
                    # Update original message to remove buttons and show error
                    await client.chat_update(
                        channel=channel,
                        ts=timestamp,
                        text="Error",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": ":warning: *Oops!*\nYou're not subscribed to standups",
                                },
                            },
                        ],
                    )
                    return

                await user_repo.update(user.id, active=False)
                await session.commit()

                # Update original message to remove buttons and show confirmation
                await client.chat_update(
                    channel=channel,
                    ts=timestamp,
                    text="Paused",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":pause_button: Standups have been paused for you. Use `/standup subscribe` to resume.",
                            },
                        }
                    ],
                )
                logger.info(f"Paused standups for user {user_id}")

        except Exception as e:
            logger.error(f"Error handling pause: {e}")
            # Try to update the message with error
            try:
                await client.chat_update(
                    channel=channel,
                    ts=timestamp,
                    text="Error",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":warning: Error: {str(e)}",
                            },
                        },
                    ],
                )
            except Exception as update_error:
                logger.error(f"Error updating message: {update_error}")
                await say(**build_error_message(f"Error: {str(e)}"))

    logger.info("Handlers registered successfully")
