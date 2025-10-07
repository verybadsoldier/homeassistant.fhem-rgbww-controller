import re
from dataclasses import dataclass

# [<h>],[<s>],[<v>] [<fadetime>] [<staytime>s]

import re
from dataclasses import dataclass
from typing import Optional
from enum import StrEnum

class QueueingPolicy(StrEnum):
    BACK: "q"
    FRONT: "f"
    FRONT_RESET: "e"

@dataclass
class AnimCommand:
    """Represents a single step in an animation sequence."""
    h: str | None = None
    s: str | None = None
    v: str | None = None
    fade: int | None = None
    fade_speed: bool = False
    stay: int | None = None
    reque: bool | None = None
    queueing_policy: QueueingPolicy | None = None
    anim_name: str | None = None


def parse_anim_command(command_str: str) -> AnimCommand:
    cmd = AnimCommand()

    parts = command_str.split(" ")

    for p in parts:
        if "," in p:
            hsv = p.split(",")
            if len(hsv) != 3:
                raise RuntimeError("HSV must have 3 parts")
            cmd.h, cmd.s, cmd.v = hsv
        else:
            is_fade_time_part = False
            if p.startswith("s") and p[1:].isdigit():
                is_fade_time_part = True
                cmd.fade_speed = True

            if is_fade_time_part or p.isdigit():
                cmd.fade = int(p)
            elif p.endswith("s") and p[:-1].isdigit():
                cmd.stay = int(p[:-1])
            else:
                # Flags
                if ":" in p:
                    splits = p.split(":", 2)
                    cmd.anim_name = splits[1]
                    p = splits[0]

                if "r" in p:
                    cmd.reque = True
                if "e" in p:
                    if cmd.queueing_policy is not None:
                        raise RuntimeError("cannot use multiple queuing policy flags")
                    cmd.queueing_policy = QueueingPolicy.FRONT_RESET
                if "f" in p:
                    if cmd.queueing_policy is not None:
                        raise RuntimeError("cannot use multiple queuing policy flags")
                    cmd.queueing_policy = QueueingPolicy.FRONT
                if "q" in p:
                    if cmd.queueing_policy is not None:
                        raise RuntimeError("cannot use multiple queuing policy flags")
                    cmd.queueing_policy = QueueingPolicy.BACK
    return cmd





def parse_anim_command_(command_str: str) -> Optional[AnimCommand]:
    """
    Parses a command string into an AnimCommand object.

    The expected format is: "[<h>],[<s>],[<v>] [<fadetime>] [<staytime>s]"
    - HSV values are strings to allow for relative values like '+50'.
    - Fade and stay times are integers in milliseconds.
    - The parser is flexible with whitespace.

    Args:
        command_str: The string to parse (e.g., "+50,, 300 5000s").

    Returns:
        An AnimCommand object if parsing is successful, otherwise None.
    """
    if not command_str:
        return None

    # Use regex to robustly separate the HSV part from the time part.
    # This handles cases where there might be no space between them.
    # Group 1: The entire HSV section (e.g., "124,5,12" or "+50,,")
    # Group 2: The rest of the string (e.g., "5000" or "300 5000s")
    match = re.match(r'([^ ]+)(.*)', command_str.strip())
    if not match:
        return None

    hsv_part, time_part = match.groups()

    # --- Parse the HSV part ---
    hsv_values = hsv_part.split(',')
    if len(hsv_values) != 3:
        # Must have exactly three components for H, S, V
        return None

    h, s, v = hsv_values

    # --- Parse the Time part ---
    fade_ms = 0
    stay_ms = 0

    # Clean up and split the time part by whitespace
    time_components = time_part.strip().split()

    try:
        for component in time_components:
            if not component:
                continue

            if component.lower().endswith('s'):
                # This is a 'stay' time
                stay_ms = int(component[:-1])
            else:
                # This is a 'fade' time
                fade_ms = int(component)
    except ValueError:
        # A component was not a valid number (e.g., "abc")
        return None

    return AnimCommand(h=h, s=s, v=v, fade=fade_ms, stay=stay_ms)


def parse_animation_commands(commands: str) -> list:
    return [parse_anim_command(x) for x in commands.split(";")]

# --- Example Usage ---
if __name__ == "__main__":
    test_strings = [
        "+50,, 300 5000s",
        "124,5,12 5000",
        "120,80,100",
        ",,100 2000s",
        "-10,+20,-30 100 500s",
        "  10,20,30   600  700s  ", # Test with extra whitespace
        "50,50,50",
        # Invalid cases
        "120,50",
        "120,50,60 abc",
        "",
        " , , ", # This is valid: three empty strings
    ]

    print("--- Testing AnimCommand Parser ---")
    for test_str in test_strings:
        result = parse_anim_command(test_str)
        print(f"Input: '{test_str}' -> Output: {result}")


