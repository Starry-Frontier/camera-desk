"""Supported storage containers and their actual encoders."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaFormat:
    suffix: str
    label: str
    extensions: tuple
    fourcc: str = ''

    @property
    def file_filter(self):
        return self.label + ' (' + ' '.join('*' + ext for ext in self.extensions) + ')'


IMAGE_FORMATS = (
    MediaFormat('.png', 'PNG 图片', ('.png',)),
    MediaFormat('.jpg', 'JPEG 图片', ('.jpg', '.jpeg')),
    MediaFormat('.bmp', 'BMP 图片', ('.bmp',)),
    MediaFormat('.tif', 'TIFF 图片', ('.tif', '.tiff')),
    MediaFormat('.webp', 'WebP 图片', ('.webp',)),
)
VIDEO_FORMATS = (
    MediaFormat('.avi', 'AVI 视频', ('.avi',), 'MJPG'),
    MediaFormat('.mp4', 'MP4 视频', ('.mp4',), 'mp4v'),
    MediaFormat('.mkv', 'MKV 视频', ('.mkv',), 'MJPG'),
)


def formats_for(kind):
    return IMAGE_FORMATS if kind == 'photo' else VIDEO_FORMATS


def format_for_extension(kind, suffix):
    for spec in formats_for(kind):
        if str(suffix).lower() in spec.extensions:
            return spec
    supported = '、'.join(spec.suffix for spec in formats_for(kind))
    raise ValueError(('照片' if kind == 'photo' else '录像') + '支持的格式为：' + supported)


def format_for_filter(kind, file_filter):
    return next((spec for spec in formats_for(kind) if spec.file_filter == file_filter), None)


def change_suffix(raw_path, kind, suffix):
    """Honor a chosen format while keeping the user's custom basename."""
    path = Path(raw_path)
    suffix = format_for_extension(kind, suffix).suffix
    known = {ext for spec in IMAGE_FORMATS + VIDEO_FORMATS for ext in spec.extensions}
    if path.suffix.lower() in known:
        return path.with_suffix(suffix)
    return path.with_name(path.name + suffix)
