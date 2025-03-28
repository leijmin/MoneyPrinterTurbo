import os
import json
import re
import subprocess
import shutil
from loguru import logger


class VideoMetadataExtractor:
    """
    视频元数据提取器，用于获取视频的各种元数据信息
    """
    
    @staticmethod
    def is_mediainfo_available() -> bool:
        """
        检查系统中是否安装了mediainfo工具
        
        Returns:
            bool: 如果mediainfo可用返回True，否则返回False
        """
        try:
            # 使用which命令检查，同时测试mediainfo命令是否可执行
            result = shutil.which("mediainfo")
            if result:
                # 进一步验证能否正常执行
                test_cmd = ["mediainfo", "--Version"]
                test_result = subprocess.run(test_cmd, capture_output=True, timeout=2)
                return test_result.returncode == 0
            return False
        except Exception as e:
            logger.warning(f"⚠️ 检查mediainfo可用性时出错: {str(e)}")
            return False
    
    @staticmethod
    def normalize_rotation(rotation: float) -> int:
        """
        标准化旋转角度（确保是90的倍数，并且为正值）
        
        Args:
            rotation: 原始旋转角度
            
        Returns:
            标准化后的旋转角度
        """
        try:
            # 确保rotation是数值
            rotation_float = float(rotation)
            rotation = int(round(rotation_float / 90) * 90) % 360
            if rotation < 0:
                rotation = (360 + rotation) % 360
            return rotation
        except (ValueError, TypeError):
            logger.warning(f"⚠️ 标准化旋转角度失败，输入值: {rotation}，使用默认值0")
            return 0
    
    @staticmethod
    def get_metadata_with_mediainfo(video_path: str) -> dict:
        """
        使用mediainfo获取视频元数据
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            包含视频元数据的字典，如果获取失败则返回空字典
        """
        # 初始化空的元数据字典，确保所有字段都有默认值
        metadata = {
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
        
        try:
            # 记录文件路径日志
            logger.info(f"🎬 使用mediainfo获取元数据 | 路径: {video_path}")
            
            # 检查文件是否存在
            if not os.path.exists(video_path):
                logger.error(f"❌ 文件不存在: {video_path}")
                return metadata
            
            # 执行mediainfo命令
            mediainfo_cmd = ["mediainfo", "--Output=JSON", video_path]
            logger.debug(f"🔍 执行命令: {' '.join(mediainfo_cmd)}")
            
            mediainfo_result = subprocess.run(
                mediainfo_cmd, 
                capture_output=True, 
                encoding='utf-8', 
                errors='replace',
                timeout=30  # 添加超时设置
            )
            
            # 检查命令执行结果
            if mediainfo_result.returncode != 0:
                logger.error(f"❌ mediainfo执行失败: {mediainfo_result.stderr}")
                return metadata
            
            stdout_text = mediainfo_result.stdout
            
            # 确保输出不为空
            if not stdout_text:
                logger.error("❌ mediainfo输出为空")
                return metadata
            
            # 解析JSON输出
            try:
                mediainfo_data = json.loads(stdout_text)
            except json.JSONDecodeError as e:
                logger.error(f"❌ 解析mediainfo JSON输出失败: {str(e)}")
                return metadata
            
            # 查找视频流
            for track in mediainfo_data.get("media", {}).get("track", []):
                if track.get("@type") == "Video":
                    # 提取基本信息，使用安全的类型转换
                    try:
                        metadata["width"] = int(track.get("Width", "0").replace(" pixels", "").split('.')[0])
                    except (ValueError, TypeError, AttributeError):
                        logger.warning("⚠️ 无法解析视频宽度")
                    
                    try:
                        metadata["height"] = int(track.get("Height", "0").replace(" pixels", "").split('.')[0])
                    except (ValueError, TypeError, AttributeError):
                        logger.warning("⚠️ 无法解析视频高度")
                    
                    # 提取编码信息
                    metadata["codec"] = track.get("Format", "unknown").lower()
                    
                    # 提取旋转信息
                    rotation = 0
                    if "Rotation" in track:
                        try:
                            rotation_str = track["Rotation"]
                            # 处理可能的字符串格式，如"90.0°"
                            if isinstance(rotation_str, str):
                                rotation_str = rotation_str.replace("°", "")
                            rotation = float(rotation_str)
                            metadata["rotation"] = VideoMetadataExtractor.normalize_rotation(rotation)
                            logger.info(f"🔄 从mediainfo获取到旋转值: {metadata['rotation']}°")
                        except (ValueError, TypeError) as e:
                            logger.warning(f"⚠️ 解析旋转值失败: {str(e)}")
                    
                    # 计算宽高比
                    if metadata["height"] > 0:
                        metadata["aspect_ratio"] = metadata["width"] / metadata["height"]
                    
                    # 判断是否为4K视频
                    metadata["is_4k"] = metadata["width"] >= 3840 or metadata["height"] >= 3840
                    
                    # 判断是否为HEVC编码
                    codec_lower = metadata["codec"].lower()
                    metadata["is_hevc"] = "hevc" in codec_lower or "h265" in codec_lower
                    
                    # 判断是否为标准横屏
                    metadata["is_standard_landscape"] = 1.7 < metadata["aspect_ratio"] < 1.8
                    
                    # 考虑旋转后的实际方向
                    effective_width, effective_height = metadata["width"], metadata["height"]
                    if metadata["rotation"] in [90, 270, -90]:
                        effective_width, effective_height = metadata["height"], metadata["width"]
                    
                    metadata["effective_width"] = effective_width
                    metadata["effective_height"] = effective_height
                    
                    # 判断是否为竖屏
                    metadata["is_portrait"] = effective_height > effective_width
                    
                    # 发现有效数据后就可以返回了
                    break
            
            # 记录获取的元数据信息
            logger.info(f"🎬 通过mediainfo获取的元数据: 宽={metadata['width']}, " +
                       f"高={metadata['height']}, 旋转={metadata['rotation']}°, " +
                       f"编码={metadata['codec']}")
            
            return metadata
        except subprocess.TimeoutExpired:
            logger.error("❌ mediainfo执行超时")
            return metadata
        except Exception as e:
            logger.error(f"❌ 使用mediainfo获取元数据失败: {str(e)}", exc_info=True)
            return metadata
    
    @staticmethod
    def get_video_rotation(video_path: str) -> int:
        """
        获取视频旋转元数据，优先使用mediainfo
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            视频旋转角度，如果未找到返回0
        """
        try:
            logger.info(f"🔄 获取视频旋转信息 | 路径: {video_path}")
            
            # 检查文件是否存在
            if not os.path.exists(video_path):
                logger.error(f"❌ 文件不存在: {video_path}")
                return 0
            
            # 优先使用mediainfo获取旋转信息
            if VideoMetadataExtractor.is_mediainfo_available():
                logger.info("✅ 检测到mediainfo可用，优先使用mediainfo获取旋转信息")
                
                try:
                    mediainfo_cmd = ["mediainfo", "--Output=JSON", video_path]
                    mediainfo_result = subprocess.run(
                        mediainfo_cmd, 
                        capture_output=True, 
                        encoding='utf-8', 
                        errors='replace',
                        timeout=30
                    )
                    
                    if mediainfo_result.returncode == 0:
                        mediainfo_data = json.loads(mediainfo_result.stdout)
                        for track in mediainfo_data.get("media", {}).get("track", []):
                            if track.get("@type") == "Video" and "Rotation" in track:
                                try:
                                    rotation_str = track["Rotation"]
                                    # 处理可能的字符串格式，如"90.0°"
                                    if isinstance(rotation_str, str):
                                        rotation_str = rotation_str.replace("°", "")
                                    rotation = float(rotation_str)
                                    normalized_rotation = VideoMetadataExtractor.normalize_rotation(rotation)
                                    logger.info(f"🔄 从mediainfo找到旋转值: {normalized_rotation}°")
                                    return normalized_rotation
                                except (ValueError, TypeError) as e:
                                    logger.warning(f"⚠️ mediainfo旋转值解析失败: {str(e)}")
                except (FileNotFoundError, json.JSONDecodeError, subprocess.SubprocessError) as e:
                    logger.warning(f"⚠️ mediainfo处理异常: {str(e)}")
            else:
                logger.warning("⚠️ 系统未安装mediainfo或mediainfo不可用，降级为使用ffprobe获取旋转信息")
            
            # 降级为使用ffprobe方法
            # 检查文件扩展名，对MOV文件特殊处理
            _, ext = os.path.splitext(video_path)
            is_mov = ext.lower() == '.mov'
            
            # 获取完整的视频信息 - 使用ffprobe
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-print_format", "json",
                video_path
            ]
            
            logger.debug(f"🔍 执行命令: {' '.join(cmd)}")
            
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            
            if result.returncode != 0:
                error_message = result.stderr
                logger.error(f"❌ ffprobe执行失败: {error_message}")
                return 0
            
            # 确保输出不为空
            stdout_text = result.stdout
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
                        try:
                            rotation = float(side_data.get("rotation", 0))
                            logger.info(f"🔄 从Display Matrix获取到旋转值: {rotation}°")
                            return VideoMetadataExtractor.normalize_rotation(rotation)
                        except (ValueError, TypeError):
                            pass
            
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
            
            # 5. 如果前面方法都没找到，尝试直接搜索文本中的旋转信息
            if "rotation of -90" in stdout_text:
                logger.info("🔄 从文本中找到 'rotation of -90'")
                return 90
            elif "rotation of 90" in stdout_text:
                logger.info("🔄 从文本中找到 'rotation of 90'")
                return 270
            elif "rotation of 180" in stdout_text or "rotation of -180" in stdout_text:
                logger.info("🔄 从文本中找到 'rotation of 180'")
                return 180
            
            # 6. 使用ffmpeg命令提取更详细的元数据
            try:
                meta_cmd = ["ffmpeg", "-i", video_path, "-hide_banner"]
                meta_result = subprocess.run(meta_cmd, capture_output=True, encoding='utf-8', errors='replace')
                meta_text = meta_result.stderr  # ffmpeg将信息输出到stderr
                
                # 搜索旋转信息的各种模式
                rotation_patterns = [
                    r'rotate\s*:\s*(-?\d+(?:\.\d+)?)',
                    r'rotation\s*:\s*(-?\d+(?:\.\d+)?)',
                    r'Rotation\s*:\s*(-?\d+(?:\.\d+)?)'
                ]
                
                for pattern in rotation_patterns:
                    matches = re.search(pattern, meta_text, re.IGNORECASE)
                    if matches:
                        try:
                            rotation = float(matches.group(1))
                            normalized_rotation = VideoMetadataExtractor.normalize_rotation(rotation)
                            logger.info(f"🔄 从ffmpeg元数据找到旋转值: {normalized_rotation}°")
                            return normalized_rotation
                        except (ValueError, TypeError):
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
        获取视频编码格式和详细信息，优先使用mediainfo
        
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
            
            # 优先使用mediainfo
            if VideoMetadataExtractor.is_mediainfo_available():
                logger.info("✅ 检测到mediainfo可用，优先使用mediainfo获取编码信息")
                
                try:
                    mediainfo_cmd = ["mediainfo", "--Output=JSON", video_path]
                    mediainfo_result = subprocess.run(mediainfo_cmd, capture_output=True, encoding='utf-8', errors='replace')
                    
                    if mediainfo_result.returncode == 0:
                        mediainfo_data = json.loads(mediainfo_result.stdout)
                        
                        for track in mediainfo_data.get("media", {}).get("track", []):
                            if track.get("@type") == "Video":
                                codec_name = track.get("Format", "unknown")
                                profile = track.get("Format_Profile", "")
                                pix_fmt = track.get("ColorSpace", "")
                                
                                codec_info = codec_name
                                if profile:
                                    codec_info += f" ({profile})"
                                if pix_fmt:
                                    codec_info += f", {pix_fmt}"
                                
                                logger.info(f"🎬 通过mediainfo获取的视频编码: {codec_info}")
                                return codec_info
                except (FileNotFoundError, json.JSONDecodeError, subprocess.SubprocessError) as e:
                    logger.warning(f"⚠️ mediainfo处理异常: {str(e)}")
            else:
                logger.warning("⚠️ 系统未安装mediainfo，降级为使用ffprobe获取编码信息")
            
            # 降级为使用ffprobe
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
                    
                    logger.info(f"🎬 通过ffprobe获取的视频编码: {codec_info}")
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
        获取视频元数据，优先使用mediainfo
        
        Args:
            video_path: 视频文件路径
            
        Returns:
            包含视频元数据的字典
        """
        try:
            logger.info(f"🎬 获取视频元数据 | 路径: {video_path}")
            
            if not os.path.exists(video_path):
                logger.error(f"❌ 文件不存在: {video_path}")
                return {
                    "width": 0, "height": 0, "rotation": 0, "aspect_ratio": 0,
                    "codec": "unknown", "is_portrait": False, "is_4k": False,
                    "is_hevc": False, "is_standard_landscape": False,
                    "effective_width": 0, "effective_height": 0
                }
            
            # 优先使用mediainfo
            if VideoMetadataExtractor.is_mediainfo_available():
                logger.info("✅ 检测到mediainfo可用，优先使用mediainfo获取元数据")
                
                mediainfo_metadata = VideoMetadataExtractor.get_metadata_with_mediainfo(video_path)
                if mediainfo_metadata and mediainfo_metadata.get("width", 0) > 0:
                    logger.info(f"🎬 使用mediainfo成功获取视频元数据: 宽={mediainfo_metadata['width']}, " +
                              f"高={mediainfo_metadata['height']}, 旋转={mediainfo_metadata['rotation']}°")
                    return mediainfo_metadata
                else:
                    logger.warning("⚠️ mediainfo获取元数据失败，降级为使用ffprobe")
            else:
                logger.warning("⚠️ 系统未安装mediainfo，降级为使用ffprobe获取元数据")
            
            # 使用ffprobe作为备选方案
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
            
            try:
                data = json.loads(stdout_text)
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON解析失败: {e}")
                return {
                    "width": 0, "height": 0, "rotation": 0, "aspect_ratio": 0,
                    "codec": "unknown", "is_portrait": False, "is_4k": False,
                    "is_hevc": False, "is_standard_landscape": False,
                    "effective_width": 0, "effective_height": 0
                }
            
            # 查找视频流
            video_stream = None
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break
                    
            if not video_stream:
                return {
                    "width": 0, "height": 0, "rotation": 0, "aspect_ratio": 0,
                    "codec": "unknown", "is_portrait": False, "is_4k": False,
                    "is_hevc": False, "is_standard_landscape": False,
                    "effective_width": 0, "effective_height": 0
                }
            
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
            
            metadata = {
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
            
            logger.info(f"🎬 使用ffprobe获取视频元数据: 宽={width}, 高={height}, " +
                      f"旋转={rotation}°, 编码={codec}")
            return metadata
            
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