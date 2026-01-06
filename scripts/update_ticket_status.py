#!/usr/bin/env python3
"""
Helper script to update ticket status in docs/tasks.md

Usage:
    python scripts/update_ticket_status.py DANUBE-1 in-progress
    python scripts/update_ticket_status.py DANUBE-1 done
    python scripts/update_ticket_status.py DANUBE-1 todo
"""

import re
import sys
from pathlib import Path

STATUS_MAP = {
    'todo': '🔴 Todo',
    'in-progress': '🟡 In Progress',
    'done': '🟢 Done',
}

def update_ticket_status(ticket_id: str, new_status: str):
    """Update the status of a ticket in docs/tasks.md"""

    if new_status not in STATUS_MAP:
        print(f"Error: Invalid status '{new_status}'")
        print(f"Valid statuses: {', '.join(STATUS_MAP.keys())}")
        return False

    tasks_file = Path('docs/tasks.md')
    if not tasks_file.exists():
        print(f"Error: {tasks_file} not found")
        return False

    content = tasks_file.read_text()

    # Pattern to match the ticket header and status line
    pattern = rf'(#### {re.escape(ticket_id)}:.*?\n\n)\*\*Status:\*\* (?:🔴 Todo|🟡 In Progress|🟢 Done)'

    matches = list(re.finditer(pattern, content))
    if not matches:
        print(f"Error: Ticket {ticket_id} not found in {tasks_file}")
        return False

    if len(matches) > 1:
        print(f"Warning: Multiple matches found for {ticket_id}")

    # Replace the status
    new_status_text = STATUS_MAP[new_status]
    updated_content = re.sub(
        pattern,
        rf'\1**Status:** {new_status_text}',
        content
    )

    tasks_file.write_text(updated_content)
    print(f"✓ Updated {ticket_id} status to {new_status_text}")
    return True


def main():
    if len(sys.argv) != 3:
        print("Usage: update_ticket_status.py TICKET_ID STATUS")
        print("Example: update_ticket_status.py DANUBE-1 in-progress")
        print(f"Valid statuses: {', '.join(STATUS_MAP.keys())}")
        sys.exit(1)

    ticket_id = sys.argv[1].upper()
    new_status = sys.argv[2].lower()

    success = update_ticket_status(ticket_id, new_status)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
