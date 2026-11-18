from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RectGeometry:
    left_in: float
    top_in: float
    width_in: float
    height_in: float


# Standard 16:9 Presentation Dimensions in inches
SLIDE_WIDTH_IN = 13.333
SLIDE_HEIGHT_IN = 7.5

# Institutional CAMES / FMSB Color Palette
COLORS = {
    "primary_burgundy": (108, 40, 38),       # #6C2826 (FMSB brand)
    "primary_burgundy_light": (245, 238, 238), # #F5EEEE
    "navy_slate": (30, 41, 59),               # #1E293B
    "slate_muted": (100, 116, 139),          # #64748B
    "card_bg_light": (248, 250, 252),        # #F8FAFC
    "card_border": (226, 232, 240),          # #E2E8F0
    "accent_gold": (217, 119, 6),            # #D97706
    "accent_gold_light": (254, 243, 199),     # #FEF3C7
    "pure_white": (255, 255, 255),
}

TEMPLATE_CATALOG: Dict[str, Dict[str, Any]] = {
    "tpl-cover-fmsb": {
        "id": "tpl-cover-fmsb",
        "name": "Page de Garde Paysage FMSB",
        "category": "cover",
        "description": "En-têtes bilatéraux étirés, conteneur noble de titre centré, promoteurs et impétrant.",
        "max_elements": 6,
    },
    "tpl-plan-bento": {
        "id": "tpl-plan-bento",
        "name": "Plan du Diaporama (5 Piliers)",
        "category": "plan",
        "description": "5 conteneurs de grands axes structurés avec badges de numérotation.",
        "max_elements": 5,
    },
    "tpl-standard-card": {
        "id": "tpl-standard-card",
        "name": "Cartes Puces Standards",
        "category": "content",
        "description": "En-tête avec breadcrumb et 2 à 4 grandes puces en cartes distinctes.",
        "max_elements": 5,
    },
    "tpl-split-media": {
        "id": "tpl-split-media",
        "name": "Contenu Split + Figure Droite",
        "category": "content",
        "description": "Moitié gauche puces synthétiques (52%), moitié droite illustration / diagramme (44%).",
        "max_elements": 4,
    },
    "tpl-stat-highlights": {
        "id": "tpl-stat-highlights",
        "name": "Métriques Épidémiologiques",
        "category": "content",
        "description": "Grille de cartes bento avec grands chiffres clés (44pt) pour l'épidémiologie.",
        "max_elements": 4,
    },
    "tpl-transition-chapter": {
        "id": "tpl-transition-chapter",
        "name": "Transition de Chapitre",
        "category": "transition",
        "description": "Grand conteneur noble annonçant le passage à une étape majeure du protocole.",
        "max_elements": 2,
    },
    "tpl-acknowledgements": {
        "id": "tpl-acknowledgements",
        "name": "Remerciements Bilingues",
        "category": "acknowledgements",
        "description": "Cadre de remerciement officiel CAMES avec traduction anglaise et contact impétrant.",
        "max_elements": 3,
    },
}


def calculate_bullet_layout(
    count: int,
    has_media: bool = False,
    is_stat_grid: bool = False,
) -> List[RectGeometry]:
    """Calculate exact geometric coordinates for background container cards and foreground textboxes."""
    count = max(1, min(count, 5))
    geometries: List[RectGeometry] = []

    if has_media:
        # Left half for cards
        left_in = 0.8
        width_in = 6.2
        avail_height = 5.2
        card_h = (avail_height - (count - 1) * 0.22) / count
        for i in range(count):
            top_in = 1.6 + i * (card_h + 0.22)
            geometries.append(RectGeometry(left_in, top_in, width_in, card_h))
    elif is_stat_grid and count in (2, 3, 4):
        # 2x2 or row grid for epidemiological statistics
        if count == 2:
            geometries.append(RectGeometry(0.8, 2.0, 5.6, 3.8))
            geometries.append(RectGeometry(6.9, 2.0, 5.6, 3.8))
        elif count == 3:
            w = 3.65
            for i in range(3):
                geometries.append(RectGeometry(0.8 + i * (w + 0.35), 2.0, w, 4.0))
        elif count == 4:
            w = 5.6
            h = 2.2
            geometries.append(RectGeometry(0.8, 1.8, w, h))
            geometries.append(RectGeometry(6.9, 1.8, w, h))
            geometries.append(RectGeometry(0.8, 4.4, w, h))
            geometries.append(RectGeometry(6.9, 4.4, w, h))
    else:
        # Full width stacked cards
        left_in = 0.8
        width_in = 11.733
        avail_height = 5.0
        card_h = min(1.35, (avail_height - (count - 1) * 0.25) / count)
        for i in range(count):
            top_in = 1.7 + i * (card_h + 0.25)
            geometries.append(RectGeometry(left_in, top_in, width_in, card_h))

    return geometries
