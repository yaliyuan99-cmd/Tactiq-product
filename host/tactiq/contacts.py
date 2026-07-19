"""The eight contact points of Table 3, and the Table 4 test conditions.

Single source of truth for contact naming across capture, segmentation and
export. Contacts are ordered anatomically (index->pinky, tip before base) so
that the within-finger pairs the paper predicts will confuse (section 3.5)
sit in adjacent 2x2 blocks on the confusion-matrix diagonal.

Naming bridge: the paper says tip/base; the website API says knuckle
top/bottom (POST /diagnostics/gesture-test/{testId}/record). `finger` and
`knuckle` here use the API's vocabulary so exports need no translation.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Contact:
    id: int        # row/column index in the confusion matrix
    key: str       # canonical key used in filenames and CSV columns
    finger: str    # API field: index | middle | ring | pinky
    knuckle: str   # API field: top (paper: tip) | bottom (paper: base)
    command: str   # the command this contact carries (Table 3)
    spoken: str    # what the audio prompt says


CONTACTS = [
    Contact(0, "index_tip",   "index",  "top",    "Confirm",        "index tip"),
    Contact(1, "index_base",  "index",  "bottom", "Dismiss / back", "index base"),
    Contact(2, "middle_tip",  "middle", "top",    "Undo",           "middle tip"),
    Contact(3, "middle_base", "middle", "bottom", "Next",           "middle base"),
    Contact(4, "ring_tip",    "ring",   "top",    "Read / repeat",  "ring tip"),
    Contact(5, "ring_base",   "ring",   "bottom", "Previous",       "ring base"),
    Contact(6, "pinky_tip",   "pinky",  "top",
            "Quick action 1 (tap) / Emergency (5 s hold)", "pinky tip"),
    Contact(7, "pinky_base",  "pinky",  "bottom", "Quick action 2", "pinky base"),
]

BY_KEY = {c.key: c for c in CONTACTS}
KEYS = [c.key for c in CONTACTS]

# Table 4: "seated, walking, cane in the other hand, phone pocketed,
# incidental hand movement"
CONDITIONS = [
    "seated",
    "walking",
    "cane_other_hand",
    "phone_pocketed",
    "incidental_movement",
]
