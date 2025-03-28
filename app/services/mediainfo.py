import os
import json
import subprocess
import shutil
from loguru import logger
from typing import Dict, Any, Optional


class MediaInfoExtractor:
    """使用MediaInfo工具提取视频元数据的实现类"""
    
    @staticmethod
    def is_available() -> bool:
        """检查系统中是否安装了mediainfo工具"""
        try:
            result = shutil.which("mediainfo")
            if result:
                test_cmd = ["mediainfo", "--Version"]
                test_result = subprocess.run(test_cmd, capture_output=True, timeout=2)
                return test_result.returncode == 0
            return False
        except Exception as e:
            logger.warning(f"⚠️ 检查mediainfo可用性时出错: {str(e)}")
            return False
    
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
    def _execute_mediainfo(file_path: str, timeout: int = 30) -> Optional[Dict]:
        """执行mediainfo命令并返回JSON数据"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"❌ 文件不存在: {file_path}")
                return None
            
            mediainfo_cmd = ["mediainfo", "--Output=JSON", file_path]
            logger.debug(f"🔍 执行命令: {' '.join(mediainfo_cmd)}")
            
            result = subprocess.run(
                mediainfo_cmd, 
                capture_output=True, 
                encoding='utf-8', 
                errors='replace',
                timeout=timeout
            )
            
            if result.returncode != 0:
                logger.error(f"❌ mediainfo执行失败: {result.stderr}")
                return None
            
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.error(f"❌ 解析mediainfo JSON输出失败: {str(e)}")
                return None
        except Exception as e:
            logger.error(f"❌ mediainfo执行异常: {str(e)}")
            return None
    
    @staticmethod
    def get_basic_metadata(file_path: str) -> Dict[str, Any]:
        """
        获取媒体文件的基本元数据（宽高、编码、旋转角度等）
        
        Args:
            file_path: 媒体文件路径
            
        Returns:
            包含基本元数据的字典
        """
        # 初始化基本元数据字典
        metadata = {
            "width": 0,
            "height": 0,
            "rotation": 0,
            "codec": "unknown",
            "aspect_ratio": 0.0,
            "duration": 0.0,
            "effective_width": 0,
            "effective_height": 0,
            "is_portrait": False
        }
        
        # 获取mediainfo JSON数据
        mediainfo_data = MediaInfoExtractor._execute_mediainfo(file_path)
        if not mediainfo_data:
            return metadata
        
        # 提取总体信息（特别是时长）
        for track in mediainfo_data.get("media", {}).get("track", []):
            if track.get("@type") == "General":
                if "Duration" in track:
                    try:
                        metadata["duration"] = float(track["Duration"])
                    except (ValueError, TypeError):
                        pass
                break
        
        # 查找视频流
        for track in mediainfo_data.get("media", {}).get("track", []):
            if track.get("@type") == "Video":
                # 提取基本信息，使用安全的类型转换
                try:
                    width_str = track.get("Width", "0")
                    if isinstance(width_str, str):
                        width_str = width_str.replace(" pixels", "").split('.')[0]
                    metadata["width"] = int(width_str)
                except (ValueError, TypeError, AttributeError):
                    logger.warning("⚠️ 无法解析视频宽度")
                
                try:
                    height_str = track.get("Height", "0")
                    if isinstance(height_str, str):
                        height_str = height_str.replace(" pixels", "").split('.')[0]
                    metadata["height"] = int(height_str)
                except (ValueError, TypeError, AttributeError):
                    logger.warning("⚠️ 无法解析视频高度")
                
                # 提取编码信息
                metadata["codec"] = track.get("Format", "unknown").lower()
                
                # 提取旋转信息
                if "Rotation" in track:
                    try:
                        rotation_str = track["Rotation"]
                        # 处理可能的字符串格式，如"90.0°"
                        if isinstance(rotation_str, str):
                            rotation_str = rotation_str.replace("°", "")
                        rotation = float(rotation_str)
                        metadata["rotation"] = MediaInfoExtractor.normalize_rotation(rotation)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"⚠️ 解析旋转值失败: {str(e)}")
                
                # 如果视频流有时长但总体信息没有，使用视频流时长
                if metadata["duration"] == 0.0 and "Duration" in track:
                    try:
                        metadata["duration"] = float(track["Duration"])
                    except (ValueError, TypeError):
                        pass
                
                # 计算宽高比
                if metadata["height"] > 0:
                    metadata["aspect_ratio"] = metadata["width"] / metadata["height"]
                
                # 计算有效尺寸（考虑旋转）
                effective_width, effective_height = metadata["width"], metadata["height"]
                if metadata["rotation"] in [90, 270]:
                    effective_width, effective_height = metadata["height"], metadata["width"]
                
                metadata["effective_width"] = effective_width
                metadata["effective_height"] = effective_height
                
                # 判断是否为竖屏
                metadata["is_portrait"] = effective_height > effective_width
                
                # 找到视频流后跳出循环
                break
        
        logger.info(f"🎬 MediaInfo基本元数据获取成功: 宽={metadata['width']}, 高={metadata['height']}, " + 
                   f"旋转={metadata['rotation']}°, 编码={metadata['codec']}")
        
        return metadata
    
    @staticmethod
    def get_detailed_metadata(file_path: str) -> Dict[str, Any]:
        """
        获取媒体文件的详细元数据（包括帧率、时长、音频信息等）
        
        Args:
            file_path: 媒体文件路径
            
        Returns:
            包含详细元数据的字典
        """
        # 获取基本元数据
        metadata = MediaInfoExtractor.get_basic_metadata(file_path)
        
        # 添加详细元数据的默认值
        detailed_metadata = {
            **metadata,  # 包含基本元数据
            "framerate": 0.0,
            "bit_depth": 0,
            "color_space": "",
            "pixel_format": "",
            "audio_codec": "unknown",
            "audio_channels": 0,
            "audio_sample_rate": 0,
            "audio_duration": 0.0,
            "is_4k": False,
            "is_hevc": False,
            "is_standard_landscape": False
        }
        
        # 获取mediainfo JSON数据
        mediainfo_data = MediaInfoExtractor._execute_mediainfo(file_path)
        if not mediainfo_data:
            return detailed_metadata
        
        # 处理视频流详细信息
        for track in mediainfo_data.get("media", {}).get("track", []):
            if track.get("@type") == "Video":
                # 提取帧率
                if "FrameRate" in track:
                    try:
                        detailed_metadata["framerate"] = float(track["FrameRate"])
                    except (ValueError, TypeError):
                        pass
                
                # 提取色彩信息
                detailed_metadata["color_space"] = track.get("ColorSpace", "")
                detailed_metadata["pixel_format"] = track.get("ChromaSubsampling", "")
                
                # 提取位深度
                if "BitDepth" in track:
                    try:
                        detailed_metadata["bit_depth"] = int(track["BitDepth"])
                    except (ValueError, TypeError):
                        pass
                
                # 判断是否为4K视频
                width = detailed_metadata["width"]
                height = detailed_metadata["height"]
                detailed_metadata["is_4k"] = width >= 3840 or height >= 3840
                
                # 判断是否为HEVC编码
                codec = detailed_metadata["codec"].lower()
                detailed_metadata["is_hevc"] = "hevc" in codec or "h265" in codec
                
                # 判断是否为标准横屏
                aspect_ratio = detailed_metadata["aspect_ratio"]
                detailed_metadata["is_standard_landscape"] = 1.7 < aspect_ratio < 1.8
            
            # 处理音频流
            elif track.get("@type") == "Audio":
                try:
                    detailed_metadata["audio_codec"] = track.get("Format", "unknown")
                    detailed_metadata["audio_channels"] = int(track.get("Channels", "0"))
                    
                    if "SamplingRate" in track:
                        sr_str = track["SamplingRate"]
                        if isinstance(sr_str, str):
                            sr_str = sr_str.replace(" Hz", "")
                        detailed_metadata["audio_sample_rate"] = int(sr_str)
                    
                    if "Duration" in track:
                        detailed_metadata["audio_duration"] = float(track["Duration"])
                except (ValueError, TypeError) as e:
                    logger.warning(f"⚠️ 解析音频元数据时出错: {str(e)}")
        
        logger.info(f"🎬 MediaInfo详细元数据获取成功: 帧率={detailed_metadata['framerate']:.2f}fps, " + 
                   f"时长={detailed_metadata['duration']:.2f}秒")
        
        return detailed_metadata
    
    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        """
        获取音频文件时长
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            音频时长（秒），获取失败返回0.0
        """
        # 获取mediainfo JSON数据
        mediainfo_data = MediaInfoExtractor._execute_mediainfo(audio_path, timeout=15)
        if not mediainfo_data:
            return 0.0
        
        # 先查找总体信息
        for track in mediainfo_data.get("media", {}).get("track", []):
            if track.get("@type") == "General" and "Duration" in track:
                try:
                    duration = float(track["Duration"])
                    logger.info(f"🎵 音频时长: {duration:.2f}秒")
                    return duration
                except (ValueError, TypeError):
                    pass
        
        # 再查找音频流信息
        for track in mediainfo_data.get("media", {}).get("track", []):
            if track.get("@type") == "Audio" and "Duration" in track:
                try:
                    duration = float(track["Duration"])
                    logger.info(f"🎵 音频时长: {duration:.2f}秒")
                    return duration
                except (ValueError, TypeError):
                    pass
        
        logger.warning("⚠️ 未能从mediainfo获取音频时长")
        return 0.0
    
    @staticmethod
    def get_video_framerate(video_path: str) -> float:
        """获取视频帧率"""
        metadata = MediaInfoExtractor.get_detailed_metadata(video_path)
        framerate = metadata.get("framerate", 0.0)
        
        # 如果未能获取帧率，使用默认值
        if framerate <= 0:
            logger.warning(f"⚠️ 未能获取视频帧率，使用默认值30fps")
            return 30.0
        
        logger.info(f"📊 视频帧率: {framerate:.2f}fps")
        return framerate

