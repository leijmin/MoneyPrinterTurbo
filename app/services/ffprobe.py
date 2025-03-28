import os
import json
import re
import subprocess
import shutil
from loguru import logger
from typing import Dict, Any, Optional

class FFprobeExtractor:
    """使用FFprobe工具提取视频元数据的实现类"""
    
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
    def _execute_ffprobe(file_path: str, args: list, timeout: int = 30) -> Optional[Dict]:
        """执行ffprobe命令并返回JSON数据"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"❌ 文件不存在: {file_path}")
                return None
            
            cmd = ["ffprobe"] + args + [file_path]
            logger.debug(f"🔍 执行命令: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                encoding='utf-8', 
                errors='replace',
                timeout=timeout
            )
            
            if result.returncode != 0:
                logger.error(f"❌ ffprobe执行失败: {result.stderr}")
                return None
            
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.error(f"❌ 解析ffprobe JSON输出失败: {str(e)}")
                return None
        except Exception as e:
            logger.error(f"❌ ffprobe执行异常: {str(e)}")
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
        
        # 获取基本流信息
        args = [
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name,r_frame_rate,duration",
            "-show_entries", "format=duration",
            "-of", "json"
        ]
        
        data = FFprobeExtractor._execute_ffprobe(file_path, args)
        if not data:
            return metadata
        
        # 提取格式信息（特别是时长）
        format_info = data.get("format", {})
        if "duration" in format_info:
            try:
                metadata["duration"] = float(format_info["duration"])
            except (ValueError, TypeError):
                pass
        
        # 提取视频流信息
        streams = data.get("streams", [])
        if streams:
            video_stream = streams[0]  # 我们选择了v:0，所以只有一个流
            
            # 提取宽高
            metadata["width"] = int(video_stream.get("width", 0))
            metadata["height"] = int(video_stream.get("height", 0))
            
            # 提取编码
            metadata["codec"] = video_stream.get("codec_name", "unknown").lower()
            
            # 如果格式中没有时长，尝试从视频流获取
            if metadata["duration"] == 0.0 and "duration" in video_stream:
                try:
                    metadata["duration"] = float(video_stream["duration"])
                except (ValueError, TypeError):
                    pass
        
        # 获取旋转信息
        metadata["rotation"] = FFprobeExtractor.extract_rotation(file_path)
        
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
        
        logger.info(f"🎬 FFprobe基本元数据获取成功: 宽={metadata['width']}, 高={metadata['height']}, " + 
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
        metadata = FFprobeExtractor.get_basic_metadata(file_path)
        
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
        
        # 获取详细信息
        args = [
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json"
        ]
        
        data = FFprobeExtractor._execute_ffprobe(file_path, args)
        if not data:
            return detailed_metadata
        
        # 处理视频流
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if video_stream:
            # 提取帧率
            if "r_frame_rate" in video_stream:
                fps_parts = video_stream["r_frame_rate"].split('/')
                if len(fps_parts) == 2 and int(fps_parts[1]) != 0:
                    detailed_metadata["framerate"] = float(int(fps_parts[0])) / float(int(fps_parts[1]))
                elif len(fps_parts) == 1:
                    detailed_metadata["framerate"] = float(fps_parts[0])
            
            # 提取像素格式和色彩空间
            detailed_metadata["pixel_format"] = video_stream.get("pix_fmt", "")
            detailed_metadata["color_space"] = video_stream.get("color_space", "")
            
            # 提取位深度
            if "bits_per_raw_sample" in video_stream and video_stream["bits_per_raw_sample"]:
                try:
                    detailed_metadata["bit_depth"] = int(video_stream["bits_per_raw_sample"])
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
        audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
        if audio_stream:
            detailed_metadata["audio_codec"] = audio_stream.get("codec_name", "unknown")
            detailed_metadata["audio_channels"] = int(audio_stream.get("channels", 0))
            detailed_metadata["audio_sample_rate"] = int(audio_stream.get("sample_rate", 0))
            
            if "duration" in audio_stream:
                detailed_metadata["audio_duration"] = float(audio_stream["duration"])
        
        logger.info(f"🎬 FFprobe详细元数据获取成功: 帧率={detailed_metadata['framerate']:.2f}fps, " + 
                   f"时长={detailed_metadata['duration']:.2f}秒")
        
        return detailed_metadata
    
    @staticmethod
    def extract_rotation(file_path: str) -> int:
        """提取视频旋转角度信息"""
        try:
            # 尝试从流标签中获取rotation
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream_tags=rotate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            
            if result.returncode == 0 and result.stdout.strip():
                try:
                    rotation = int(float(result.stdout.strip()))
                    return FFprobeExtractor.normalize_rotation(rotation)
                except ValueError:
                    pass
            
            # 检查side_data_list中的Display Matrix
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_streams",
                "-of", "json",
                file_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
                
                if video_stream:
                    side_data_list = video_stream.get("side_data_list", [])
                    for side_data in side_data_list:
                        if side_data.get("side_data_type") == "Display Matrix":
                            rotation = float(side_data.get("rotation", 0))
                            return FFprobeExtractor.normalize_rotation(rotation)
            
            # 使用ffmpeg命令检查（最后的尝试）
            cmd = ["ffmpeg", "-i", file_path, "-hide_banner"]
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            stderr_text = result.stderr
            
            rotation_patterns = [
                r'rotate\s*:\s*(-?\d+(?:\.\d+)?)',
                r'rotation\s*:\s*(-?\d+(?:\.\d+)?)',
                r'Rotation\s*:\s*(-?\d+(?:\.\d+)?)'
            ]
            
            for pattern in rotation_patterns:
                matches = re.search(pattern, stderr_text, re.IGNORECASE)
                if matches:
                    try:
                        rotation = float(matches.group(1))
                        return FFprobeExtractor.normalize_rotation(rotation)
                    except (ValueError, TypeError):
                        pass
            
            return 0  # 默认返回0表示没有旋转
        except Exception as e:
            logger.error(f"❌ 提取视频旋转信息失败: {str(e)}")
            return 0
    
    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        """
        获取音频文件时长
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            音频时长（秒），获取失败返回0.0
        """
        args = [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json"
        ]
        
        data = FFprobeExtractor._execute_ffprobe(audio_path, args, timeout=15)
        if not data:
            return 0.0
        
        format_info = data.get("format", {})
        if "duration" in format_info:
            try:
                duration = float(format_info["duration"])
                logger.info(f"🎵 音频时长: {duration:.2f}秒")
                return duration
            except (ValueError, TypeError):
                pass
        
        logger.warning("⚠️ 未能从ffprobe获取音频时长")
        return 0.0
    
    @staticmethod
    def get_video_framerate(video_path: str) -> float:
        """获取视频帧率"""
        metadata = FFprobeExtractor.get_detailed_metadata(video_path)
        framerate = metadata.get("framerate", 0.0)
        
        # 如果未能获取帧率，使用默认值
        if framerate <= 0:
            logger.warning(f"⚠️ 未能获取视频帧率，使用默认值30fps")
            return 30.0
        
        logger.info(f"📊 视频帧率: {framerate:.2f}fps")
        return framerate
