from dataclasses import dataclass
from enum import StrEnum

# [<h>],[<s>],[<v>] [<fadetime>] [<staytime>s]
import re
from typing import Optional


class _QueueingPolicy(StrEnum):
    BACK = "back"
    FRONT = "front"
    FRONT_RESET = "front_reset"
    SINGLE = "single"


@dataclass
class AnimCommand:
    """Represents a single step in an animation sequence."""

    h: str | None = None
    s: str | None = None
    v: str | None = None
    ct: str | None = None
    fade: int | None = None
    fade_speed: bool = False
    stay: int | None = None
    reque: bool | None = None
    queueing_policy: _QueueingPolicy | None = None
    anim_name: str | None = None
    direction_long: bool = False  # True if long, False if short

    def to_dict(self) -> dict:
        data = {"hsv": {}}

        if self.h:
            data["hsv"]["h"] = self.h

        if self.s:
            data["hsv"]["s"] = self.s

        if self.v:
            data["hsv"]["v"] = self.v

        if self.ct:
            data["hsv"]["ct"] = self.ct

        if self.fade_speed:
            if self.fade is not None:
                data["s"] = self.fade
        elif self.fade is not None:
            data["t"] = self.fade

        if self.stay is not None:
            data["stay"] = self.stay

        if self.queueing_policy:
            data["q"] = self.queueing_policy.value

        if self.anim_name:
            data["name"] = self.anim_name

        if self.reque:
            data["r"] = True

        if self.direction_long:
            data["d"] = "long"

        return data


def parse_anim_command(command_str: str) -> AnimCommand:
    cmd = AnimCommand()

    parts = command_str.split(" ")

    for p in parts:
        if "," in p:
            hsv_parts = (
                p.split(",") + [None] * 4
            )  # pad with None to ensure at least 4 parts

            # replace empty strings with None
            hsv_parts = [part if part != "" else None for part in hsv_parts]

            cmd.h, cmd.s, cmd.v, cmd.ct = hsv_parts[:4]  # take only first 4 parts
        else:
            is_fade_time_part = False
            if p.startswith("s") and p[1:].isdigit():
                is_fade_time_part = True
                cmd.fade_speed = True

            if is_fade_time_part or p.isdigit():
                cmd.fade = float(p) * 1000
            elif p.endswith("s") and p[:-1].isdigit():
                cmd.stay = float(p[:-1]) * 1000
            else:
                # Flags
                if ":" in p:
                    splits = p.split(":", 2)
                    cmd.anim_name = splits[1]
                    p = splits[0]

                if "r" in p:
                    cmd.reque = True
                if "d" in p:
                    cmd.direction_long = True
                if "e" in p:
                    if cmd.queueing_policy is not None:
                        raise RuntimeError("cannot use multiple queuing policy flags")
                    cmd.queueing_policy = _QueueingPolicy.FRONT_RESET
                if "f" in p:
                    if cmd.queueing_policy is not None:
                        raise RuntimeError("cannot use multiple queuing policy flags")
                    cmd.queueing_policy = _QueueingPolicy.FRONT
                if "q" in p:
                    if cmd.queueing_policy is not None:
                        raise RuntimeError("cannot use multiple queuing policy flags")
                    cmd.queueing_policy = _QueueingPolicy.BACK
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
    match = re.match(r"([^ ]+)(.*)", command_str.strip())
    if not match:
        return None

    hsv_part, time_part = match.groups()

    # --- Parse the HSV part ---
    hsv_values = hsv_part.split(",")
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

            if component.lower().endswith("s"):
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
        "  10,20,30   600  700s  ",  # Test with extra whitespace
        "50,50,50",
        # Invalid cases
        "120,50",
        "120,50,60 abc",
        "",
        " , , ",  # This is valid: three empty strings
    ]

    print("--- Testing AnimCommand Parser ---")
    for test_str in test_strings:
        result = parse_anim_command(test_str)
        print(f"Input: '{test_str}' -> Output: {result}")
