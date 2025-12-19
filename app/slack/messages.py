"""Slack Block Kit message builders for DM and channel messages."""

from typing import Optional, List, Dict, Any
from datetime import datetime, date
from app.utils.slack_utils import build_user_profile_link, escape_slack_text
from app.utils.timeutils import format_date_for_display


def build_standup_start_message() -> Dict[str, Any]:
    """Build the initial standup DM message (Question 1).

    Returns:
        Block Kit message payload
    """
    return {
        "text": "Daily Standup",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":wave: Hey! Time for today's standup.\n\nFirst question: *How are you feeling today?*",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Skip Today"},
                        "value": "skip_today",
                        "action_id": "button_skip_today",
                        "style": "danger",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Pause Standups"},
                        "value": "pause_standups",
                        "action_id": "button_pause_standups",
                    },
                ],
            },
        ],
    }


def build_question_message(question_index: int, message_text: str, previous_report_today: Optional[str] = None) -> Dict[str, Any]:
    """Build a question message for DM.

    Args:
        question_index: 0=feeling, 1=yesterday, 2=today, 3=blockers
        message_text: The question text
        previous_report_today: Optional text from previous report's 'today' answer

    Returns:
        Block Kit message payload
    """
    questions = [
        "How are you feeling today?",
        "What did you do yesterday?",
        "What are you doing today?",
        "Anything blocking your progress?",
    ]

    if question_index < 0 or question_index > 3:
        question_index = 0

    question_title = questions[question_index]

    # Questions 1 and 3 (yesterday and today) use simple text input
    if question_index in [1, 2, 3]:
        # For question 1 (yesterday), include previous report's today if available
        question_text = question_title
        if question_index == 1 and previous_report_today:
            question_text += f"\n\nIn your previous report you mentioned: _{escape_slack_text(previous_report_today)}_"
        
        return {
            "text": f"Standup Q{question_index + 1}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Question {question_index + 1} of 4:*\n{question_text}\n\nJust type your answer below and press Enter!",
                    },
                },
                # {
                #     "type": "actions",
                #     "elements": [
                #         {
                #             "type": "button",
                #             "text": {"type": "plain_text", "text": "Skip Today"},
                #             "value": "skip_today",
                #             "action_id": "button_skip_today",
                #             "style": "danger",
                #         },
                #     ],
                # },
            ],
        }
    
    # Questions 0 and 3 (feeling and blockers) use text input with submit button
    return {
        "text": f"Standup Q{question_index + 1}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Question {question_index + 1} of 4:*\n{question_title}",
                },
            },
            {
                "type": "input",
                "block_id": f"input_q{question_index}",
                "label": {"type": "plain_text", "text": "Your answer", "emoji": True},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"answer_q{question_index}",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Type your answer here...",
                    },
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Submit"},
                        "value": f"submit_q{question_index}",
                        "action_id": f"button_submit_q{question_index}",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Skip Today"},
                        "value": "skip_today",
                        "action_id": "button_skip_today",
                        "style": "danger",
                    },
                ],
            },
        ],
    }


def build_missed_standup_message(last_report_date: date) -> Dict[str, Any]:
    """Build a message for a missed standup.

    Args:
        last_report_date: Date of the last completed report

    Returns:
        Block Kit message payload
    """
    formatted_date = format_date_for_display(last_report_date)

    return {
        "text": "Catch up standup",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":thinking_face: Looks like you missed your last report on *{formatted_date}*.\n\nWhat did you do since then?",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Skip Today"},
                        "value": "skip_today",
                        "action_id": "button_skip_today",
                        "style": "danger",
                    },
                ],
            },
        ],
    }


def build_standup_report_message(
    user_name: str,
    slack_user_id: str,
    feeling: Optional[str],
    yesterday: Optional[str],
    today: Optional[str],
    blockers: Optional[str],
    timestamp: datetime,
) -> Dict[str, Any]:
    """
    Build a Slack standup report message with colored sections
    for yesterday / today / blockers (Geekbot-style).
    """

    def build_colored_question_attachment(
        question: str,
        answer: str,
        color: str,
    ) -> Dict[str, Any]:
        return {
            "color": color,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{question}*",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        # Preserve numbered lists exactly as user typed
                        "text": escape_slack_text(answer),
                    },
                },
            ],
        }

    # ─────────────────────────────────────────────
    # Main (non-colored) blocks
    # ─────────────────────────────────────────────
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":spiral_note_pad: Daily Standup",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{build_user_profile_link(slack_user_id)}\n_{user_name}_",
            },
        },
    ]

    if feeling:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":heart: Feeling: {escape_slack_text(feeling)}",
                    }
                ],
            }
        )

    # ─────────────────────────────────────────────
    # Colored attachments (one per question)
    # ─────────────────────────────────────────────
    attachments: list[Dict[str, Any]] = []

    if yesterday:
        attachments.append(
            build_colored_question_attachment(
                "What have you done since yesterday?",
                yesterday,
                "#38BDF8",  # Cyan / Blue
            )
        )

    if today:
        attachments.append(
            build_colored_question_attachment(
                "What will you do today?",
                today,
                "#A855F7",  # Purple
            )
        )

    if blockers:
        attachments.append(
            build_colored_question_attachment(
                "Anything blocking your progress?",
                blockers,
                "#F97316",  # Orange
            )
        )

    # ─────────────────────────────────────────────
    # Footer
    # ─────────────────────────────────────────────
    timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M %Z")
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Posted by *Daily Standup Bot* • {timestamp_str}",
                }
            ],
        }
    )

    return {
        "text": f"Daily Standup - {user_name}",
        "blocks": blocks,
        "attachments": attachments,
    }




def build_skip_notification_message(user_name: str, slack_user_id: str) -> Dict[str, Any]:
    """Build a message when user skips today.

    Args:
        user_name: User's display name
        slack_user_id: Slack user ID

    Returns:
        Block Kit message payload
    """
    return {
        "text": f"{user_name} skipped today",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{build_user_profile_link(slack_user_id)} skipped standup today",
                },
            }
        ],
    }


def build_error_message(error_text: str) -> Dict[str, Any]:
    """Build an error message for DM.

    Args:
        error_text: Error description

    Returns:
        Block Kit message payload
    """
    return {
        "text": "Standup Error",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *Oops!*\n{error_text}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Please try again later or contact support.",
                    }
                ],
            },
        ],
    }


def build_completion_message() -> Dict[str, Any]:
    """Build a message confirming standup completion.

    Returns:
        Block Kit message payload
    """
    return {
        "text": "Standup Complete",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":white_check_mark: *Thanks!* Your standup has been posted. Great job!",
                },
            }
        ],
    }
