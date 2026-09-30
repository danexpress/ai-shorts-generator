from .tiktok import TikTokPublisher
from .x import XPublisher
from .youtube import YouTubePublisher


def publishers(settings, http):
    return {
        "youtube": YouTubePublisher(settings, http),
        "tiktok": TikTokPublisher(settings, http),
        "x": XPublisher(settings, http),
    }
