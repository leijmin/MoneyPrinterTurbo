import os
import json
import re
import subprocess
import shutil
from loguru import logger
from typing import Dict, Any, Optional
from dataclasses import dataclass
from app.services.mediainfo import MediaInfoExtractor
from app.services.ffprobe import FFprobeExtractor
from app.services.cache_manager import cache_manager


@dataclass
class VideoBasicMetadata:
    """视频基础元数据"""
    width: int = 0
    height: int = 0
    rotation: int = 0
    aspect_ratio: float = 0.0
    duration: float = 0.0
    effective_width: int = 0
    effective_height: int = 0
    is_portrait: bool = False
    codec: str = "unknown"
    
    def __getitem__(self, key):
        """支持字典语法访问类属性"""
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(f"属性 '{key}' 不存在")
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VideoBasicMetadata':
        """从字典创建元数据对象"""
        return cls(
            width=data.get("width", 0),
            height=data.get("height", 0),
            rotation=data.get("rotation", 0),
            aspect_ratio=data.get("aspect_ratio", 0.0),
            duration=data.get("duration", 0.0),
            effective_width=data.get("effective_width", 0),
            effective_height=data.get("effective_height", 0),
            is_portrait=data.get("is_portrait", False),
            codec=data.get("codec", "unknown")
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典用于缓存"""
        return {
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "aspect_ratio": self.aspect_ratio,
            "duration": self.duration,
            "effective_width": self.effective_width,
            "effective_height": self.effective_height,
            "is_portrait": self.is_portrait,
            "codec": self.codec
        }


@dataclass
class VideoDetailedMetadata(VideoBasicMetadata):
    """视频完整元数据"""
    framerate: float = 0.0
    bit_depth: int = 0
    color_space: str = ""
    pixel_format: str = ""
    audio_codec: str = "unknown"
    audio_channels: int = 0
    audio_sample_rate: int = 0
    audio_duration: float = 0.0
    is_4k: bool = False
    is_hevc: bool = False
    is_standard_landscape: bool = False
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VideoDetailedMetadata':
        """从字典创建详细元数据对象"""
        basic = super(VideoDetailedMetadata, cls).from_dict(data)
        
        return cls(
            width=basic.width,
            height=basic.height,
            rotation=basic.rotation,
            aspect_ratio=basic.aspect_ratio,
            duration=basic.duration,
            effective_width=basic.effective_width,
            effective_height=basic.effective_height,
            is_portrait=basic.is_portrait,
            codec=basic.codec,
            framerate=data.get("framerate", 0.0),
            bit_depth=data.get("bit_depth", 0),
            color_space=data.get("color_space", ""),
            pixel_format=data.get("pixel_format", ""),
            audio_codec=data.get("audio_codec", "unknown"),
            audio_channels=data.get("audio_channels", 0),
            audio_sample_rate=data.get("audio_sample_rate", 0),
            audio_duration=data.get("audio_duration", 0.0),
            is_4k=data.get("is_4k", False),
            is_hevc=data.get("is_hevc", False),
            is_standard_landscape=data.get("is_standard_landscape", False)
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典用于缓存"""
        basic_dict = super().to_dict()
        detailed_dict = {
            "framerate": self.framerate,
            "bit_depth": self.bit_depth,
            "color_space": self.color_space,
            "pixel_format": self.pixel_format,
            "audio_codec": self.audio_codec,
            "audio_channels": self.audio_channels,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_duration": self.audio_duration,
            "is_4k": self.is_4k,
            "is_hevc": self.is_hevc,
            "is_standard_landscape": self.is_standard_landscape
        }
        return {**basic_dict, **detailed_dict}


class VideoMetadataExtractor:
    """
    视频元数据提取器，提供统一的API接口，内部根据可用性选择MediaInfo或FFprobe实现
    """
    
    @staticmethod
    def is_mediainfo_available() -> bool:
        """检查系统中是否安装了mediainfo工具"""
        return MediaInfoExtractor.is_available()
    
    @staticmethod
    def normalize_rotation(rotation: float) -> int:
        """标准化旋转角度（确保是90的倍数，并且为正值）"""
        try:
            rotation_float = float(rotation)
            rotation = int(round(rotation_float / 90) * 90) % 360
            if rotation < 0:
                rotation = (360 + rotation) % 360
            return rotation
        except (ValueError, TypeError):
            logger.warning(f"⚠️ 标准化旋转角度失败，输入值: {rotation}，使用默认值0")
            return 0
    
    @staticmethod
    def get_basic_metadata(video_path: str) -> VideoBasicMetadata:
        """
        获取视频基础元数据
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            包含基本元数据的 VideoBasicMetadata 对象
        """
        logger.info(f"🎬 获取视频基础元数据 | 路径: {video_path}")
        
        # 检查文件是否存在
        if not os.path.exists(video_path):
            logger.error(f"❌ 文件不存在: {video_path}")
            return VideoBasicMetadata(error="文件不存在")
        
        # 尝试从缓存获取
        cached_data = cache_manager.get_metadata(video_path, "basic")
        if cached_data:
            return VideoBasicMetadata.from_dict(cached_data)
        
        # 缓存未命中，从媒体信息提取器获取
        metadata_dict = {}
        if VideoMetadataExtractor.is_mediainfo_available():
            logger.info("✅ 使用MediaInfo获取视频基础元数据")
            metadata_dict = MediaInfoExtractor.get_basic_metadata(video_path)
        else:
            logger.info("⚠️ MediaInfo不可用，使用FFprobe获取视频基础元数据")
            metadata_dict = FFprobeExtractor.get_basic_metadata(video_path)
        
        # 缓存获取的元数据
        cache_manager.set_metadata(video_path, "basic", metadata_dict)
        
        # 返回类实例
        return VideoBasicMetadata.from_dict(metadata_dict)
    
    @staticmethod
    def get_video_metadata(video_path: str) -> Dict[str, Any]:
        """获取视频的完整元数据"""
        logger.info(f"🎬 获取视频完整元数据 | 路径: {video_path}")
        
        # 检查文件是否存在
        if not os.path.exists(video_path):
            logger.error(f"❌ 文件不存在: {video_path}")
            return {"width": 0, "height": 0, "rotation": 0, "error": "文件不存在", 
                    "codec": "unknown", "is_portrait": False, "effective_width": 0, 
                    "effective_height": 0, "aspect_ratio": 0.0, "duration": 0.0}
        
        # 尝试从缓存获取
        cached_data = cache_manager.get_metadata(video_path, "detailed")
        if cached_data:
            return cached_data
        
        # 缓存未命中，从媒体信息提取器获取
        metadata_dict = {}
        if VideoMetadataExtractor.is_mediainfo_available():
            logger.info("✅ 使用MediaInfo获取视频元数据")
            metadata_dict = MediaInfoExtractor.get_detailed_metadata(video_path)
        else:
            logger.info("⚠️ MediaInfo不可用，使用FFprobe获取视频元数据")
            metadata_dict = FFprobeExtractor.get_detailed_metadata(video_path)
        
        # 缓存获取的元数据
        cache_manager.set_metadata(video_path, "detailed", metadata_dict)
        
        return metadata_dict
    
    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        """获取音频文件时长"""
        logger.info(f"🎵 获取音频时长 | 路径: {audio_path}")
        
        if not os.path.exists(audio_path):
            logger.error(f"❌ 音频文件不存在: {audio_path}")
            return 0.0
        
        # 尝试从缓存获取
        cached_data = cache_manager.get_metadata(audio_path, "basic")
        if cached_data and "duration" in cached_data:
            return float(cached_data["duration"])
        
        # 缓存未命中，从提取器获取
        duration = 0.0
        if VideoMetadataExtractor.is_mediainfo_available():
            logger.info("✅ 使用MediaInfo获取音频时长")
            duration = MediaInfoExtractor.get_audio_duration(audio_path)
        else:
            logger.info("⚠️ MediaInfo不可用，使用FFprobe获取音频时长")
            duration = FFprobeExtractor.get_audio_duration(audio_path)
        
        # 缓存获取的时长
        cache_manager.set_metadata(audio_path, "basic", {"duration": duration})
        
        return duration
    
    @staticmethod
    def get_video_framerate(video_path: str) -> float:
        """获取视频帧率"""
        logger.info(f"📊 获取视频帧率 | 路径: {video_path}")
        
        if not os.path.exists(video_path):
            logger.warning(f"❌ 视频文件不存在: {video_path}")
            return 30.0  # 返回默认帧率
        
        # 尝试从缓存获取
        cached_data = cache_manager.get_metadata(video_path, "detailed")
        if cached_data and "framerate" in cached_data:
            framerate = float(cached_data["framerate"])
            if framerate > 0:
                return framerate
        
        # 完整获取视频元数据（会写入缓存）
        metadata = VideoMetadataExtractor.get_video_metadata(video_path)
        framerate = metadata.get("framerate", 30.0)
        
        # 如果未能获取帧率，使用默认值
        if framerate <= 0:
            logger.warning(f"⚠️ 未能获取视频帧率，使用默认值30fps")
            return 30.0
        
        logger.info(f"📊 视频帧率: {framerate:.2f}fps")
        return framerate
    
    @staticmethod
    def get_video_rotation(video_path: str) -> int:
        """获取视频旋转角度"""
        metadata = VideoMetadataExtractor.get_video_metadata(video_path)
        rotation = metadata.get("rotation", 0)
        logger.info(f"🔄 视频旋转角度: {rotation}°")
        return rotation
    
    @staticmethod
    def get_video_codec(video_path: str) -> str:
        """获取视频编码格式"""
        metadata = VideoMetadataExtractor.get_video_metadata(video_path)
        codec = metadata.get("codec", "unknown")
        logger.info(f"🎬 视频编码: {codec}")
        return codec
    
    @staticmethod
    def is_portrait_by_metadata(width: int, height: int, rotation: int) -> bool:
        """根据视频的宽、高和旋转角度判断视频是否为竖屏"""
        if rotation in [90, 270]:
            # 考虑旋转，交换宽高
            width, height = height, width
        
        # 旋转后高度大于宽度即为竖屏
        return height > width
