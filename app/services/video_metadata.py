import os
import json
import re
import subprocess
from loguru import logger


class VideoMetadataExtractor:
    """
    视频元数据提取器，用于获取视频的各种元数据信息
    """
    
    @staticmethod
    def normalize_rotation(rotation: float) -> int:
        """
        标准化旋转角度（确保是90的倍数，并且为正值）
        
        Args:
            rotation: 原始旋转角度
            
        Returns:
            标准化后的旋转角度
        """
        rotation = int(round(rotation / 90) * 90) % 360
        if rotation < 0:
            rotation = (360 + rotation) % 360
        return rotation
    
    @staticmethod
    def get_video_rotation(video_path: str) -> int:
        """
        获取视频旋转元数据，支持多种格式的旋转信息
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            视频旋转角度，如果未找到返回0
        """
        try:
            logger.info(f"🔄 获取视频旋转信息 | 路径: {video_path}")
            
            # 首先记录文件是否存在
            if not os.path.exists(video_path):
                logger.error(f"❌ 文件不存在: {video_path}")
                return 0
            
            # 检查文件扩展名，对MOV文件特殊处理
            _, ext = os.path.splitext(video_path)
            is_mov = ext.lower() == '.mov'
            if is_mov:
                logger.info("检测到MOV文件，尝试特殊处理方式获取旋转信息")
                
                # MOV文件使用mediainfo可能更准确
                try:
                    mediainfo_cmd = ["mediainfo", "--Output=JSON", video_path]
                    mediainfo_result = subprocess.run(mediainfo_cmd, capture_output=True, encoding='utf-8', errors='replace')
                    if mediainfo_result.returncode == 0:
                        mediainfo_data = json.loads(mediainfo_result.stdout)
                        for track in mediainfo_data.get("media", {}).get("track", []):
                            if track.get("@type") == "Video" and "Rotation" in track:
                                try:
                                    rotation = int(float(track["Rotation"]))
                                    logger.info(f"🔄 从mediainfo找到MOV文件旋转值: {rotation}°")
                                    return VideoMetadataExtractor.normalize_rotation(rotation)
                                except (ValueError, KeyError):
                                    pass
                except (FileNotFoundError, json.JSONDecodeError, subprocess.SubprocessError):
                    # mediainfo可能不存在，继续尝试其他方法
                    pass
            
            # 获取完整的视频信息 - 首先使用常规方法
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-print_format", "json",
                video_path
            ]
            
            logger.debug(f"🔍 执行命令: {' '.join(cmd)}")
            
            # 使用二进制模式，避免编码问题
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            
            if result.returncode != 0:
                error_message = result.stderr
                logger.error(f"❌ ffprobe执行失败: {error_message}")
                return 0
            
            # 解码输出
            stdout_text = result.stdout
            
            # 确保输出不为空
            if not stdout_text:
                logger.error("❌ ffprobe输出为空")
                return 0
            
            # 解析JSON
            try:
                data = json.loads(stdout_text)
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON解析失败: {e}")
                return 0
            
            # 查找视频流
            video_stream = None
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
            
            if not video_stream:
                logger.error("❌ 未找到视频流")
                return 0
            
            # 1. 从tags中获取旋转信息
            rotation = 0
            tags = video_stream.get("tags", {})
            if tags and "rotate" in tags:
                try:
                    rotation_str = tags.get("rotate", "0")
                    rotation = int(float(rotation_str))
                    logger.info(f"🔄 从tags.rotate获取到旋转值: {rotation}°")
                    return VideoMetadataExtractor.normalize_rotation(rotation)
                except ValueError as e:
                    logger.warning(f"⚠️ 解析rotate值失败: {e}")
            
            # 2. 检查side_data_list中的Display Matrix
            side_data_list = video_stream.get("side_data_list", [])
            for side_data in side_data_list:
                if side_data.get("side_data_type") == "Display Matrix":
                    if "rotation" in side_data:
                        rotation = float(side_data.get("rotation", 0))
                        logger.info(f"🔄 从Display Matrix获取到旋转值: {rotation}°")
                        return VideoMetadataExtractor.normalize_rotation(rotation)
            
            # 3. 如果还没找到，直接在JSON文本中查找Rotation字段
            if "Rotation" in stdout_text or "rotation" in stdout_text.lower():
                # 尝试使用正则表达式匹配旋转信息
                rotation_matches = re.findall(r'[Rr]otation\D*(\d+)', stdout_text)
                if rotation_matches:
                    try:
                        rotation = int(rotation_matches[0])
                        logger.info(f"🔄 从文本匹配找到旋转值: {rotation}°")
                        return VideoMetadataExtractor.normalize_rotation(rotation)
                    except ValueError:
                        pass

            # 4. 尝试使用另一种格式获取旋转信息
            alt_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream_tags=rotate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            ]
            
            alt_result = subprocess.run(alt_cmd, capture_output=True, encoding='utf-8', errors='replace')
            if alt_result.returncode == 0 and alt_result.stdout.strip():
                try:
                    rotation = int(float(alt_result.stdout.strip()))
                    logger.info(f"🔄 从stream_tags找到旋转值: {rotation}°")
                    return VideoMetadataExtractor.normalize_rotation(rotation)
                except ValueError:
                    pass
            
            # 5. 尝试mediainfo命令获取旋转信息(如果系统中安装了)
            try:
                mediainfo_cmd = ["mediainfo", "--Output=JSON", video_path]
                mediainfo_result = subprocess.run(mediainfo_cmd, capture_output=True, encoding='utf-8', errors='replace')
                if mediainfo_result.returncode == 0:
                    mediainfo_data = json.loads(mediainfo_result.stdout)
                    for track in mediainfo_data.get("media", {}).get("track", []):
                        if track.get("@type") == "Video" and "Rotation" in track:
                            try:
                                rotation = int(float(track["Rotation"]))
                                logger.info(f"🔄 从mediainfo找到旋转值: {rotation}°")
                                return VideoMetadataExtractor.normalize_rotation(rotation)
                            except (ValueError, KeyError):
                                pass
            except (FileNotFoundError, json.JSONDecodeError, subprocess.SubprocessError):
                # mediainfo可能不存在或格式不正确，忽略这些错误
                pass
            
            # 6. 如果前面方法都没找到，尝试直接搜索文本中的旋转信息
            if "rotation of -90" in stdout_text:
                logger.info("🔄 从文本中找到 'rotation of -90'")
                return 90
            elif "rotation of 90" in stdout_text:
                logger.info("🔄 从文本中找到 'rotation of 90'")
                return 270
            elif "rotation of 180" in stdout_text or "rotation of -180" in stdout_text:
                logger.info("🔄 从文本中找到 'rotation of 180'")
                return 180
            
            # 7. 使用元数据工具生成更详细的输出并搜索其中的旋转信息
            try:
                meta_cmd = ["ffmpeg", "-i", video_path, "-hide_banner"]
                meta_result = subprocess.run(meta_cmd, capture_output=True, encoding='utf-8', errors='replace')
                meta_text = meta_result.stderr  # ffmpeg将信息输出到stderr
                
                # 搜索旋转信息
                rotation_patterns = [
                    r'rotate\s*:\s*(\d+)',
                    r'rotation\s*:\s*(\d+)',
                    r'Rotation\s*:\s*(\d+)'
                ]
                
                for pattern in rotation_patterns:
                    matches = re.search(pattern, meta_text, re.IGNORECASE)
                    if matches:
                        try:
                            rotation = int(matches.group(1))
                            logger.info(f"🔄 从ffmpeg元数据找到旋转值: {rotation}°")
                            return VideoMetadataExtractor.normalize_rotation(rotation)
                        except ValueError:
                            pass
            except subprocess.SubprocessError:
                pass
            
            logger.info(f"🔄 未找到旋转信息，默认为0°")
            return 0
        
        except Exception as e:
            logger.error(f"❌ 获取视频旋转信息失败: {str(e)}", exc_info=True)
            return 0
    
    @staticmethod
    def get_video_codec(video_path: str) -> str:
        """
        获取视频编码格式和详细信息
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            视频编码信息字符串
        """
        try:
            logger.info(f"🎬 获取视频编码信息 | 路径: {video_path}")
            
            if not os.path.exists(video_path):
                logger.error(f"❌ 文件不存在: {video_path}")
                return "unknown"
            
            # 获取详细的编码信息
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,profile,pix_fmt",
                "-of", "json",
                video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            
            if result.returncode != 0:
                error_message = result.stderr
                logger.error(f"❌ 获取编码信息失败: {error_message}")
                return "unknown"
            
            try:
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                
                if streams:
                    codec_name = streams[0].get("codec_name", "unknown")
                    profile = streams[0].get("profile", "")
                    pix_fmt = streams[0].get("pix_fmt", "")
                    
                    codec_info = codec_name
                    if profile:
                        codec_info += f" ({profile})"
                    if pix_fmt:
                        codec_info += f", {pix_fmt}"
                    
                    logger.info(f"🎬 视频编码: {codec_info}")
                    return codec_info
            except Exception as e:
                logger.error(f"❌ 解析编码信息失败: {str(e)}")
            
            return "unknown"
        
        except Exception as e:
            logger.error(f"❌ 获取视频编码失败: {str(e)}")
            return "unknown"
    
    @staticmethod
    def get_video_metadata(video_path: str) -> dict:
        """
        获取视频元数据
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            包含视频元数据的字典
        """
        try:
            logger.info(f"🎬 获取视频元数据 | 路径: {video_path}")
            
            # 使用与原函数相同的方法处理
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-print_format", "json",
                video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=False)
            stdout_text = result.stdout.decode('utf-8', errors='replace')
            data = json.loads(stdout_text)
            
            # 查找视频流
            video_stream = None
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
                    
            if not video_stream:
                return {"width": 0, "height": 0, "rotation": 0, "aspect_ratio": 0, "codec": "unknown"}
            
            # 获取视频尺寸
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
            codec = video_stream.get("codec_name", "unknown").lower()
            
            # 获取旋转信息
            rotation = VideoMetadataExtractor.get_video_rotation(video_path)
            
            # 计算宽高比
            aspect_ratio = width / height if height != 0 else 0
            
            # 判断是否为4K视频
            is_4k = width >= 3840 or height >= 3840
            
            # 判断是否为HEVC编码
            is_hevc = codec == 'hevc'
            
            # 判断是否为标准横屏
            is_standard_landscape = 1.7 < aspect_ratio < 1.8
            
            # 考虑旋转后的实际方向
            effective_width, effective_height = width, height
            if rotation in [90, 270, -90]:
                effective_width, effective_height = height, width
            
            # 判断是否为竖屏
            is_portrait = effective_height > effective_width
            
            return {
                "width": width,
                "height": height,
                "effective_width": effective_width,
                "effective_height": effective_height,
                "rotation": rotation,
                "aspect_ratio": aspect_ratio,
                "codec": codec,
                "is_portrait": is_portrait,
                "is_4k": is_4k,
                "is_hevc": is_hevc,
                "is_standard_landscape": is_standard_landscape
            }
            
        except Exception as e:
            logger.error(f"❌ 获取视频元数据失败: {str(e)}", exc_info=True)
            return {
                "width": 0, 
                "height": 0, 
                "effective_width": 0,
                "effective_height": 0, 
                "rotation": 0, 
                "aspect_ratio": 0,
                "codec": "unknown",
                "is_portrait": False,
                "is_4k": False,
                "is_hevc": False,
                "is_standard_landscape": False
            }
            
    @staticmethod
    def is_portrait_by_metadata(width: int, height: int, rotation: int) -> bool:
        """
        根据视频的宽、高和旋转角度判断视频是否为竖屏
        
        Args:
            width: 视频宽度
            height: 视频高度
            rotation: 旋转角度
            
        Returns:
            是否为竖屏视频
        """
        if rotation in [90, 270, -90]:
            # 考虑旋转，交换宽高
            width, height = height, width
        
        # 旋转后高度大于宽度即为竖屏
        return height > width 