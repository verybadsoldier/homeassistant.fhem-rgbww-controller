# Animation CLI Interface Reference

The FHEM RGBWW Controller integration provides a powerful "Command Line Interface" (CLI) syntax. This syntax allows you to define complex lighting animations, relative shifts, loops, and overrides using a single, compact string.

This is highly useful for automations, scripts, and Node-RED flows where defining long YAML lists is cumbersome.

## Available Services

* **`fhem_rgbwwcontroller.animation_cli_hsv`**: Targets Hue (0-360), Saturation (0-100), Brightness/Value (0-100), and Color Temperature.
* **`fhem_rgbwwcontroller.animation_cli_rgbww`**: Targets the raw hardware channels: Red, Green, Blue, Cold White, Warm White (Raw PWM values, typically 0-1023).

---

## CLI Command Syntax

A CLI command string consists of one or more **steps**, separated by a semicolon (`;`). 

**Structure of a single step:**
`[Color] [Transition] [StayTime] [Flags]`

*(Parameters are separated by spaces. You can omit values to keep their current state).*

### 1. Color Definition (Absolute or Relative)
Values are separated by commas. You can provide absolute target values, or you can use **relative values** by adding a `+` or `-` prefix to shift the current state up or down.

* **HSV Mode (`animation_cli_hsv`)**: `H,S,V,CT`
    * *Absolute Example*: `120,100,50` (Green, 100% saturation, 50% brightness)
    * *Relative Example*: `+20,,-10` (Increase Hue by 20 degrees, keep Saturation the same, decrease Brightness by 10%)
    * *Keep State Example*: `,,0` (Leave Hue and Saturation alone, change brightness to 0)
* **RGBWW Mode (`animation_cli_rgbww`)**: `R,G,B,CW,WW`
    * *Absolute Example*: `1023,0,0,0,0` (100% Red)
    * *Relative Example*: `+50,+50,+50,,` (Increase R, G, and B hardware channels by 50)

### 2. Transition (Time or Speed)
* **Time (Default)**: A simple number represents the transition duration in **seconds**. 
    * Example: `5` (Fades to the target color over 5 seconds).
* **Speed**: A number prefixed with `s` represents the transition speed.
    * Example: `s200` (Changes Hue at 200 degrees/minute, or other channels at 200 percentage points/minute).

### 3. Stay Time
Defines how long the controller waits *after* the transition completes before moving to the next step or finishing the command.
* **Format**: A number suffixed with `s` (seconds).
* **Default**: `0s` (You do not need to provide a stay time if you want the controller to immediately proceed to the next step).
* *Example*: `10s` (Stay on the target color for 10 seconds).

---

### 4. Flags: Queue Policies vs. Modifiers

It is critical to understand that flags are divided into two distinct categories: **Queue Policies** (which tell the hardware scheduler *where* to put this step) and **Step Modifiers** (which change how the *individual step* executes). You can combine one Queue Policy with any number of Step Modifiers (e.g., `q r` means "append to the back of the queue, and requeue it when done").

#### Category A: Queue Policy Flags (Mutually Exclusive)
Every step must have exactly one queue policy. This dictates how the step interacts with any currently playing animations or queued steps. 

⚠️ **Crucial Rule for Command Sequences:** When chaining multiple steps together using semicolons (`;`), **every step after the first one must explicitly include a queue policy flag** (usually `q` for Back). If you omit the flag on subsequent steps, they default to `Single`, which will instantly clear the hardware queue and overwrite your previous steps!

| Flag | Name | Hardware Behavior |
| :--- | :--- | :--- |
| *(none)* | **Single (Default)** | **Clears the entire playback queue** and plays this step exclusively. |
| **`q`** | **Back** | Appends the step to the end of the current queue. It waits its turn. |
| **`f`** | **Front** | Injects the step at the front of the queue. The currently running animation is interrupted but will resume exactly where it left off once this step finishes. |
| **`e`** | **Front Reset** | Injects the step at the front of the queue. The currently running animation is interrupted and will restart from its absolute beginning once this step finishes. |

#### Category B: Step Modifiers (Combinable)
These flags modify the execution of the specific step they are attached to, independent of the queue policy.

| Flag | Name | Hardware Behavior |
| :--- | :--- | :--- |
| **`r`** | **Requeue** | Loops the step. Once this specific transition and stay time complete, the hardware automatically pushes this single step back into the queue. |
| **`d`** | **Direction** | Forces the transition to take the "long way" around the 360-degree color wheel (HSV Hue only). |
| **`:name:`**| **Name** | Tags the step with a custom string (e.g., `:my_effect:`). Useful for tracking hardware events. |

> **Pro-Tip: The Requeue Flag (`r`)**
> Because the `r` flag only loops the individual step it is attached to (not the entire sequence), it is incredibly powerful when combined with **relative color values**. Using absolute colors with the `r` flag just sets the light to the exact same static color indefinitely. By using relative values (like `+10`), the loop will continuously shift the color forever.

---

## Examples

### 1. Continuous Rainbow (Relative Hue + Requeue)
This is where the `r` modifier shines. We define a single step that increases the Hue by 15 degrees over 1 second, and give it the `r` flag. Because it is a relative value, the hardware cycles through the color spectrum indefinitely. *(Note: Stay time is omitted as it defaults to 0).*

```yaml
service: fhem_rgbwwcontroller.animation_cli_hsv
target:
  entity_id: light.living_room
data:
  # Hue +15 degrees, 1s transition, Requeue modifier (r)
  # Uses default 'Single' queue policy
  anim_definition_command: "+15,,, 1 r"
```

### 2. Sunrise Sequence (Absolute Colors + Queue Policy)
A sequence of three steps. Start with a dim red, transition to orange over 60 seconds, wait 5 seconds, then transition to bright warm white over 60 seconds. Note how every step after the first uses the `q` queue policy flag to chain them properly.

```yaml
service: fhem_rgbwwcontroller.animation_cli_hsv
target:
  entity_id: light.bedroom
data:
  # Step 1: Default 'Single' policy (clears queue), Named :start:, 0s default stay
  # Step 2: Queue to Back (q), 5s stay
  # Step 3: Queue to Back (q), 0s default stay
  anim_definition_command: "0,100,1 0 :start:; 30,100,50 60 5s q; ,,100,2700 60 q"
```

### 3. Immediate "Flash" Override (RGBWW)
Flash Red immediately at full brightness, interrupting whatever is currently playing, hold for 1 second, then let the previous animation continue. 

```yaml
service: fhem_rgbwwcontroller.animation_cli_rgbww
target:
  entity_id: light.hallway
data:
  # Step 1: Front Reset (e) injects to front, sets red, holds 1s
  # Step 2: Front (f) injects right behind step 1, turns off, defaults to 0s stay
  anim_definition_command: "1023,0,0,0,0 0 1s e; 0,0,0,0,0 0 f"
```

### 4. Combining Queue Policy and Modifiers
Fade to Blue over 2 seconds, queue it behind whatever is currently running, and repeat this fade infinitely once it starts.

```yaml
service: fhem_rgbwwcontroller.animation_cli_hsv
target:
  entity_id: light.living_room
data:
  # q = Queue Policy Back
  # r = Step Modifier Requeue
  # Default stay time is automatically 0s
  anim_definition_command: "240,100,100 2 q r"
```