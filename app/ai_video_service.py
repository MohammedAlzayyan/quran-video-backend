try:
    # Try moviepy 2.x imports
    from moviepy import ColorClip, TextClip, CompositeVideoClip
except ImportError:
    # Fallback to moviepy 1.x imports
    from moviepy.editor import ColorClip, TextClip, CompositeVideoClip

import random
from typing import List

# --- Smart Content Filtering System ---

# Global exclude keywords that apply to ALL scenes
GLOBAL_EXCLUDE_KEYWORDS = [
    "woman", "women", "girl", "model", "bikini", "portrait", "sexy", "lingerie", 
    "people", "person", "man", "crowd", "human", "face", "selfie",
    "church", "cross", "jesus", "christmas", "temple", "budha", "idol", "god",
    "alcohol", "wine", "beer", "bar", "club", "party", "dance", "nightclub",
    "gambling", "casino", "poker", "bet",
    "pig", "dog", "pork", "ham",
    "weapon", "gun", "war", "blood", "violence",
    "money", "cash", "bank", "wealth", "luxury"
]

SCENE_COLORS = {
    "Ocean": (0, 105, 148),      # Deep Blue
    "محيط": (0, 105, 148),
    "Forest": (34, 139, 34),     # Forest Green
    "غابة": (34, 139, 34),
    "Mountains": (105, 105, 105),# Dim Gray
    "جبال": (105, 105, 105),
    "Rivers": (0, 191, 255),     # Deep Sky Blue
    "أنهار": (0, 191, 255),
    "Desert": (210, 180, 140),   # Tan
    "صحراء": (210, 180, 140),
    "Rain": (70, 130, 180),      # Steel Blue
    "مطر": (70, 130, 180),
    "Sunset": (255, 69, 0),      # Orange Red
    "غروب": (255, 69, 0),
    "المدينة المنورة": (34, 139, 34),     # Prophet's Green
    "مساجد": (218, 165, 32),     # Golden
}

# Keywords for searching nature videos (English for Pexels API)
# Refined to be more specific to pure nature
SCENE_KEYWORDS = {
    "Ocean": "ocean waves drone landscape blue water",
    "محيط": "ocean waves drone landscape blue water",
    "Forest": "forest trees nature aerial foggy forest pine trees",
    "غابة": "forest trees nature aerial foggy forest pine trees",
    "Mountains": "mountain peak snow mountains landscape aerial nature",
    "جبال": "mountain peak snow mountains landscape aerial nature",
    "Rivers": "river flowing water stream waterfall nature",
    "أنهار": "river flowing water stream waterfall nature",
    "Desert": "desert dunes sand sahara aerial landscape",
    "صحراء": "desert dunes sand sahara aerial landscape",
    "Rain": "rain drops nature window rain macro water",
    "مطر": "rain drops nature window rain macro water",
    "Sunset": "sunset sky clouds horizon nature landscape orange sky",
    "غروب": "sunset sky clouds horizon nature landscape orange sky",
    "المدينة المنورة": "prophet's mosque medina architecture makkah medina",
    "مساجد": "mosque exterior islamic architecture dome minaret",
}

# Scene-specific exclusions (in addition to global)
SCENE_EXCLUDE_KEYWORDS = {
    "Ocean": ["bridge", "ship", "boat", "surfer", "swimmer", "beach", "coast", "city"],
    "محيط": ["bridge", "ship", "boat", "surfer", "swimmer", "beach", "coast", "city"],
    "Forest": ["cabin", "house", "road", "car", "camping", "tent"],
    "غابة": ["cabin", "house", "road", "car", "camping", "tent"],
    "Mountains": ["ski", "snowboard", "hikers", "resort", "cable car"],
    "جبال": ["ski", "snowboard", "hikers", "resort", "cable car"],
    "Rivers": ["bridge", "boat", "kayak", "fishing", "dam"],
    "أنهار": ["bridge", "boat", "kayak", "fishing", "dam"],
    "Desert": ["camel", "safari", "car", "road", "industrial"],
    "صحراء": ["camel", "safari", "car", "road", "industrial"],
    "Rain": ["umbrella", "street", "traffic", "car", "city", "people walk"],
    "مطر": ["umbrella", "street", "traffic", "car", "city", "people walk"],
    "Sunset": ["city skyline", "buildings", "street", "traffic", "airport"],
    "غروب": ["city skyline", "buildings", "street", "traffic", "airport"],
    "المدينة المنورة": ["office", "shopping", "mall", "fashion", "modern building"],
    "مساجد": ["office", "shopping", "mall", "fashion", "modern building"],
}

async def fetch_nature_clips(scenes: List[str], duration: float) -> List[str]:
    """
    Mock service to return scene metadata.
    """
    return [scenes[0] if scenes else "Ocean"]
