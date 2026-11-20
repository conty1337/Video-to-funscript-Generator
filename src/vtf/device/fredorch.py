"""Timing for the Fredorch F21S (Buttplug protocol "fredorch").

The device has 16 positions (0..15) and 20 speed steps. Intiface's driver turns
(distance, duration) into a speed with the table below: the slowest step whose
travel time is *shorter* than the duration. So the device always arrives early and
waits for the next command. We leave the step to the driver (a slower one would make
the device fall behind: it finishes a move before it takes the next) and use the
table to know when it arrives, so the next stroke can start right then.

SPEED_MATRIX from buttplug crates/buttplug_server/src/device/protocol_impl/fredorch.rs
(Copyright 2016-2026 Nonpolynomial Labs LLC, BSD 3-Clause).
"""

from __future__ import annotations

POSITIONS = 15            # steps 0..15

# travel time in ms per distance (1..15 steps, rows) and speed (1..20, columns)
SPEED_MATRIX = (
    (1000, 800, 400, 235, 200, 172, 155, 92, 60, 45, 38, 34, 32, 28, 27, 26, 25, 24, 23, 22),
    (1500, 1000, 800, 680, 600, 515, 425, 265, 165, 115, 80, 70, 50, 48, 45, 35, 34, 33, 32, 30),
    (2500, 2310, 1135, 925, 792, 695, 565, 380, 218, 155, 105, 82, 70, 68, 65, 60, 48, 45, 43, 40),
    (3000, 2800, 1500, 1155, 965, 810, 690, 465, 260, 195, 140, 110, 85, 75, 74, 73, 70, 65, 60, 55),
    (3400, 3232, 2305, 1380, 1200, 1165, 972, 565, 328, 235, 162, 132, 98, 78, 75, 74, 73, 72, 71, 70),
    (3500, 3350, 2500, 1640, 1250, 1210, 1010, 645, 385, 275, 175, 160, 115, 95, 91, 90, 85, 80, 77, 75),
    (3600, 3472, 2980, 2060, 1560, 1275, 1132, 738, 430, 310, 230, 170, 128, 122, 110, 108, 105, 103, 101, 100),
    (3800, 3500, 3055, 2105, 1740, 1370, 1290, 830, 490, 355, 235, 195, 150, 140, 135, 132, 130, 125, 120, 119),
    (3900, 3518, 3190, 2315, 2045, 1510, 1442, 1045, 552, 392, 280, 225, 172, 145, 140, 138, 135, 134, 132, 130),
    (6000, 5755, 3240, 2530, 2135, 1605, 1500, 1200, 595, 425, 285, 245, 175, 170, 160, 155, 150, 145, 142, 140),
    (6428, 5872, 3335, 2780, 2270, 1782, 1590, 1310, 648, 470, 315, 255, 182, 180, 175, 172, 170, 162, 160, 155),
    (6730, 5950, 3490, 2995, 2395, 1890, 1650, 1350, 700, 500, 350, 290, 220, 190, 185, 180, 175, 170, 165, 160),
    (6962, 6122, 3880, 3205, 2465, 1900, 1700, 1400, 835, 545, 375, 310, 228, 195, 190, 185, 182, 181, 180, 175),
    (7945, 6365, 4130, 3470, 2505, 1910, 1755, 1510, 855, 580, 400, 330, 235, 210, 205, 200, 195, 190, 185, 180),
    (8048, 7068, 4442, 3708, 2668, 1930, 1800, 1520, 878, 618, 428, 365, 260, 255, 250, 240, 230, 220, 210, 200),
)


def is_fredorch(name: str) -> bool:
    return "fredorch" in name.lower()


def step(position: float) -> int:
    """0..100 -> device step 0..15."""
    return round(max(0.0, min(100.0, position)) / 100 * POSITIONS)


def driver_speed(distance: int, duration_ms: int) -> int:
    """What the driver picks (calculate_speed in fredorch.rs): 0 = no move, else 1..20."""
    distance = min(distance, POSITIONS)
    if distance == 0:
        return 0
    row = SPEED_MATRIX[distance - 1]
    return next((s + 1 for s, t in enumerate(row) if t < duration_ms), 20)


def travel_ms(distance: int, sent_ms: int) -> int:
    """How long the move really takes when `sent_ms` is sent (0: no move)."""
    speed = driver_speed(distance, sent_ms)
    return SPEED_MATRIX[min(distance, POSITIONS) - 1][speed - 1] if speed else 0
