"""Standup service: Core business logic for standup workflow."""

import logging
from datetime import date, datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import (
    UserRepository,
    StandupReportRepository,
    StandupStateRepository,
)
from app.slack.bolt_app import get_slack_client
from app.slack.messages import (
    build_standup_start_message,
    build_question_message,
    build_missed_standup_message,
    build_standup_report_message,
    build_completion_message,
    build_skip_notification_message,
)
from app.utils.timeutils import get_user_date, get_user_datetime_now
from app.core.config import settings

logger = logging.getLogger(__name__)

# Question sequence
QUESTIONS = [
    ("feeling", "How are you feeling today?"),
    ("yesterday", "What did you do yesterday?"),
    ("today", "What are you doing today?"),
    ("blockers", "Any blockers or challenges?"),
]


async def send_pending_standups(session: AsyncSession) -> None:
    """Check for users without reports and send initial DMs.

    Args:
        session: Async database session
    """
    user_repo = UserRepository(session)
    report_repo = StandupReportRepository(session)
    state_repo = StandupStateRepository(session)
    slack_client = get_slack_client()

    # Get all active users
    users = await user_repo.list_active()
    logger.info(f"Processing {len(users)} active users for standup dispatch")

    for user in users:
        try:
            # Determine user's timezone
            user_tz = user.timezone or settings.scheduler_timezone
            user_date = get_user_date(user_tz)

            # Check if user already has a report for today
            existing_report = await report_repo.get_by_user_date(user.id, user_date)

            if existing_report:
                logger.debug(f"User {user.slack_user_id} already has report for {user_date}")
                continue

            # Check for missed reports from previous days
            latest_report = await report_repo.get_latest_by_user(user.id)
            missed_report_date = None

            if latest_report and latest_report.report_date < user_date:
                missed_report_date = latest_report.report_date

            # Send appropriate message
            if missed_report_date:
                # Send catch-up message
                logger.info(
                    f"Sending catch-up message to {user.slack_user_id} "
                    f"for date {missed_report_date}"
                )
                await _send_dm(
                    slack_client,
                    user.slack_user_id,
                    build_missed_standup_message(missed_report_date),
                )
                # Set up state for catch-up
                await state_repo.create_or_update(
                    user.id,
                    pending_report_date=missed_report_date,
                    current_question_index=0,
                )
            else:
                # Send initial standup message
                logger.info(f"Sending standup to {user.slack_user_id}")
                await _send_dm(
                    slack_client,
                    user.slack_user_id,
                    build_standup_start_message(),
                )
                # Set up state for new standup
                await state_repo.create_or_update(
                    user.id,
                    pending_report_date=user_date,
                    current_question_index=0,
                )

        except Exception as e:
            logger.error(f"Error processing user {user.slack_user_id}: {e}", exc_info=True)

    await session.commit()


async def handle_user_answer(
    session: AsyncSession,
    user_id: int,
    answer_text: str,
) -> Optional[dict]:
    """Process user's answer to current question.

    Args:
        session: Async database session
        user_id: Slack user ID (not DB ID)
        answer_text: User's answer text

    Returns:
        Dict with action (next_question, complete_report, error) or None
    """
    user_repo = UserRepository(session)
    report_repo = StandupReportRepository(session)
    state_repo = StandupStateRepository(session)

    user = await user_repo.get_by_slack_id(user_id)
    if not user:
        logger.warning(f"User not found: {user_id}")
        return {"action": "error", "message": "User not found in database"}

    state = await state_repo.get_by_user(user.id)
    if not state:
        logger.warning(f"No pending standup state for user {user_id}")
        return {"action": "error", "message": "No pending standup. Start with /standup or wait for scheduled time."}

    # Get or create report
    report = await report_repo.get_by_user_date(user.id, state.pending_report_date)
    if not report:
        report = await report_repo.create(
            user_id=user.id,
            report_date=state.pending_report_date,
        )

    # Store answer in report
    question_index = state.current_question_index
    if question_index >= len(QUESTIONS):
        logger.warning(f"Invalid question index: {question_index}")
        return {"action": "error", "message": "Invalid state"}

    question_key = QUESTIONS[question_index][0]
    setattr(report, question_key, answer_text)

    await session.flush()

    # Check if more questions remain
    state.current_question_index += 1
    if state.current_question_index >= len(QUESTIONS):
        # All questions answered - complete the report
        await report_repo.mark_completed(report.id)
        await state_repo.delete(user.id)
        await session.commit()

        logger.info(f"Standup completed for user {user_id}")
        return {"action": "complete_report", "report_id": report.id}

    # Send next question
    await session.commit()
    next_index = state.current_question_index
    logger.info(f"Moving user {user_id} to question {next_index}")

    return {"action": "next_question", "question_index": next_index}


async def handle_skip_today(
    session: AsyncSession,
    slack_user_id: str,
) -> Optional[dict]:
    """Handle user skipping today's standup.

    Args:
        session: Async database session
        slack_user_id: Slack user ID

    Returns:
        Dict with result status
    """
    user_repo = UserRepository(session)
    report_repo = StandupReportRepository(session)
    state_repo = StandupStateRepository(session)

    user = await user_repo.get_by_slack_id(slack_user_id)
    if not user:
        logger.warning(f"User not found: {slack_user_id}")
        return {"action": "error", "message": "User not found"}

    state = await state_repo.get_by_user(user.id)
    if not state:
        logger.warning(f"No pending state for user {slack_user_id}")
        return {"action": "error", "message": "No pending standup"}

    # Create or update skipped report
    report = await report_repo.get_by_user_date(user.id, state.pending_report_date)
    if report:
        # Report already exists, mark it as skipped
        await report_repo.update(report.id, skipped=True)
    else:
        # Create new skipped report
        await report_repo.create(
            user_id=user.id,
            report_date=state.pending_report_date,
            skipped=True,
        )

    # Delete state
    await state_repo.delete(user.id)
    await session.commit()

    logger.info(f"User {slack_user_id} skipped standup")

    # Optionally post skip notification to channel
    if not settings.skip_notification_to_channel:
        return {"action": "skipped"}

    try:
        slack_client = get_slack_client()
        await _post_to_channel(
            slack_client,
            settings.slack_default_channel,
            build_skip_notification_message(user.display_name, slack_user_id),
        )
    except Exception as e:
        logger.error(f"Error posting skip notification: {e}")

    return {"action": "skipped"}


async def post_report_to_channel(
    session: AsyncSession,
    report_id: int,
) -> Optional[dict]:
    """Post completed standup report to the configured channel.

    Args:
        session: Async database session
        report_id: Report ID

    Returns:
        Dict with result status
    """
    report_repo = StandupReportRepository(session)
    user_repo = UserRepository(session)
    slack_client = get_slack_client()

    # Fetch report using repository
    report = await report_repo.get_by_id(report_id)
    if not report:
        logger.warning(f"Report {report_id} not found")
        return {"action": "error", "message": "Report not found"}

    # Get user info
    user = await user_repo.get_by_id(report.user_id)
    if not user:
        logger.warning(f"User for report {report_id} not found")
        return {"action": "error", "message": "User not found"}

    try:
        # Build message
        message = build_standup_report_message(
            user_name=user.display_name,
            slack_user_id=user.slack_user_id,
            feeling=report.feeling,
            yesterday=report.yesterday,
            today=report.today,
            blockers=report.blockers,
            timestamp=datetime.utcnow(),
        )

        # Post to channel
        await _post_to_channel(
            slack_client,
            settings.slack_default_channel,
            message,
        )

        logger.info(f"Posted report {report_id} to channel {settings.slack_default_channel}")
        return {"action": "posted", "channel": settings.slack_default_channel}

    except Exception as e:
        logger.error(f"Error posting report to channel: {e}", exc_info=True)
        return {"action": "error", "message": f"Failed to post: {str(e)}"}


# Helper functions

async def _send_dm(slack_client, user_id: str, message: dict) -> None:
    """Send a direct message to a Slack user.

    Args:
        slack_client: Slack client
        user_id: Slack user ID
        message: Block Kit message dict
    """
    try:
        await slack_client.conversations_open(users=[user_id])
        await slack_client.chat_postMessage(
            channel=user_id,
            **message,
        )
        logger.debug(f"Sent DM to {user_id}")
    except Exception as e:
        logger.error(f"Failed to send DM to {user_id}: {e}")
        raise


async def _post_to_channel(slack_client, channel_id: str, message: dict) -> None:
    """Post a message to a Slack channel.

    Args:
        slack_client: Slack client
        channel_id: Channel ID
        message: Block Kit message dict
    """
    try:
        await slack_client.chat_postMessage(
            channel=channel_id,
            **message,
        )
        logger.debug(f"Posted message to {channel_id}")
    except Exception as e:
        logger.error(f"Failed to post to {channel_id}: {e}")
        raise
