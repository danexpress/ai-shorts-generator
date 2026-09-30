from .youtube import YouTubePublisher


def publishers(settings, http):
    return {"youtube": YouTubePublisher(settings, http)}
