import glob
import os
import random
from typing import List
import time
import re
import json
import subprocess
import shutil
import uuid
import math
import shlex  # 添加shlex模块导入

from loguru import logger
from PIL import ImageFont

from app.models import const
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.utils import utils


def get_bgm_file(bgm_type: str = "random", bgm_file: str = ""):
    if not bgm_type:
        return ""

    if bgm_file and os.path.exists(bgm_file):
        return bgm_file

    if bgm_type == "random":
        suffix = "*.mp3"
        song_dir = utils.song_dir()
        files = glob.glob(os.path.join(song_dir, suffix))
        return random.choice(files)

    return ""

def get_video_rotation(video_path: str) -> int:
    """获取视频旋转元数据，支持多种格式的旋转信息"""
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
                                return VideoMetadataHandler.normalize_rotation(rotation)
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
                return VideoMetadataHandler.normalize_rotation(rotation)
            except ValueError as e:
                logger.warning(f"⚠️ 解析rotate值失败: {e}")
        
        # 2. 检查side_data_list中的Display Matrix
        side_data_list = video_stream.get("side_data_list", [])
        for side_data in side_data_list:
            if side_data.get("side_data_type") == "Display Matrix":
                if "rotation" in side_data:
                    rotation = float(side_data.get("rotation", 0))
                    logger.info(f"🔄 从Display Matrix获取到旋转值: {rotation}°")
                    return VideoMetadataHandler.normalize_rotation(rotation)
        
        # 3. 如果还没找到，直接在JSON文本中查找Rotation字段
        if "Rotation" in stdout_text or "rotation" in stdout_text.lower():
            # 尝试使用正则表达式匹配旋转信息
            rotation_matches = re.findall(r'[Rr]otation\D*(\d+)', stdout_text)
            if rotation_matches:
                try:
                    rotation = int(rotation_matches[0])
                    logger.info(f"🔄 从文本匹配找到旋转值: {rotation}°")
                    return VideoMetadataHandler.normalize_rotation(rotation)
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
                return VideoMetadataHandler.normalize_rotation(rotation)
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
                            return VideoMetadataHandler.normalize_rotation(rotation)
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
                        return VideoMetadataHandler.normalize_rotation(rotation)
                    except ValueError:
                        pass
        except subprocess.SubprocessError:
            pass
        
        logger.info(f"🔄 未找到旋转信息，默认为0°")
        return 0
    
    except Exception as e:
        logger.error(f"❌ 获取视频旋转信息失败: {str(e)}", exc_info=True)
        return 0

def get_video_codec(video_path: str) -> str:
    """获取视频编码格式和详细信息"""
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
        
        result = subprocess.run(cmd, capture_output=True, text=False, encoding='utf-8', errors='replace')
        
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

def combine_videos(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    threads: int = 2,
) -> str:
    """
    合并多个视频片段为一个连续的视频
    
    Args:
        combined_video_path: 合并后的视频路径
        video_paths: 要合并的视频路径列表
        audio_file: 音频文件路径
        video_aspect: 视频宽高比
        video_concat_mode: 视频拼接模式
        video_transition_mode: 视频转场效果
        max_clip_duration: 最大单个片段时长
        threads: 处理线程数
        
    Returns:
        合并后的视频路径，失败返回None
    """
    try:
        logger.info(f"合并视频: {len(video_paths)} 个片段")
        if len(video_paths) == 0:
            logger.error(f"没有可用的视频片段，无法合并")
            return None
        
        if not audio_file or not os.path.exists(audio_file):
            logger.error(f"音频文件不存在: {audio_file}")
            return None
        
        if video_aspect not in [VideoAspect.portrait, VideoAspect.landscape]:
            logger.warning(f"不支持的视频宽高比: {video_aspect}，使用默认值: portrait")
            video_aspect = VideoAspect.portrait
        
        # 使用纯ffmpeg方法合并视频
        logger.info("使用纯ffmpeg方法合并视频")
        return combine_videos_ffmpeg(
            combined_video_path=combined_video_path,
            video_paths=video_paths,
            audio_file=audio_file,
            video_aspect=video_aspect,
            video_concat_mode=video_concat_mode,
            video_transition_mode=video_transition_mode,
            max_clip_duration=max_clip_duration,
            threads=threads
        )
            
    except Exception as e:
        logger.error(f"视频合并失败: {str(e)}", exc_info=True)
        return None

def combine_videos_ffmpeg(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    threads: int = 2,
) -> str:
    """使用纯ffmpeg实现视频合并，完全不依赖MoviePy"""
    if not video_paths:
        logger.error("没有输入视频文件")
        return None
        
    if not os.path.exists(audio_file):
        logger.error(f"音频文件不存在: {audio_file}")
        return None
    
    # 创建临时目录
    temp_dir = os.path.join(os.path.dirname(combined_video_path), f"temp_combine_{str(uuid.uuid4())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    # 记录需要清理的临时文件
    processed_paths = []
    segment_files = []
    
    try:
        # 获取音频时长
        audio_probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_file]
        audio_info = json.loads(subprocess.check_output(audio_probe_cmd, universal_newlines=True, encoding='utf-8', errors='replace'))
        audio_duration = float(audio_info["format"]["duration"])
        logger.info(f"音频时长: {audio_duration} 秒")
        
        # 设置视频分辨率
        aspect = VideoAspect(video_aspect)
        video_width, video_height = aspect.to_resolution()
        logger.info(f"目标视频分辨率: {video_width}x{video_height}")
        
        # 预处理并裁剪每个视频
        processed_segments = []
        segment_index = 0
        
        for idx, video_path in enumerate(video_paths):
            try:
                if not os.path.exists(video_path):
                    logger.error(f"视频文件不存在: {video_path}")
                    continue
                
                logger.info(f"处理视频 {idx+1}/{len(video_paths)}: {os.path.basename(video_path)}")
                
                # 获取视频信息
                info_cmd = [
                    "ffprobe", 
                    "-v", "error", 
                    "-select_streams", "v:0", 
                    "-show_entries", "stream=width,height,r_frame_rate,duration,codec_name,rotation", 
                    "-of", "json", 
                    video_path
                ]
                
                info_result = subprocess.run(info_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                
                if info_result.returncode != 0:
                    logger.error(f"获取视频信息失败: {info_result.stderr}")
                    continue
                
                try:
                    video_info = json.loads(info_result.stdout)
                    stream = video_info.get("streams", [{}])[0]
                    
                    # 获取视频宽高
                    v_width = int(stream.get("width", 0))
                    v_height = int(stream.get("height", 0))
                    
                    # 获取帧率
                    fps_str = stream.get("r_frame_rate", "30/1")
                    fps_parts = fps_str.split('/')
                    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else 30
                    
                    # 获取视频编码
                    codec = stream.get("codec_name", "")
                    
                    # 获取视频时长
                    v_duration = float(stream.get("duration", 0))
                    if v_duration <= 0:
                        # 如果流中没有时长，尝试从格式信息获取
                        format_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path]
                        format_result = subprocess.run(format_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                        try:
                            v_duration = float(format_result.stdout.strip())
                        except:
                            # 如果还是无法获取，则计算帧数/帧率
                            frames_cmd = ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", video_path]
                            frames_result = subprocess.run(frames_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                            try:
                                frame_count_str = frames_result.stdout.strip()
                                # 解析帧数，需要处理可能存在的逗号
                                frame_count = int(frame_count_str.replace(',', ''))
                                v_duration = frame_count / fps
                            except:
                                logger.warning(f"无法计算视频时长，使用默认值10秒")
                                v_duration = 10.0
                    
                    logger.info(f"视频信息: {v_width}x{v_height}, {fps:.2f}fps, {v_duration:.2f}秒, 编码: {codec}")
                    
                    # 使用更强大的旋转检测
                    metadata_rotation = 0
                    
                    # 从元数据中检查旋转信息
                    if "tags" in stream and "rotate" in stream["tags"]:
                        try:
                            metadata_rotation = int(stream["tags"]["rotate"])
                            logger.info(f"从视频元数据中检测到旋转角度: {metadata_rotation}°")
                        except (ValueError, TypeError):
                            pass
                    elif "side_data_list" in stream:
                        for side_data in stream["side_data_list"]:
                            if side_data.get("side_data_type") == "Display Matrix" and "rotation" in side_data:
                                try:
                                    metadata_rotation = int(side_data["rotation"])
                                    logger.info(f"从显示矩阵中检测到旋转角度: {metadata_rotation}°")
                                except (ValueError, TypeError):
                                    pass
                    
                    # 获取旋转信息
                    rotation = get_video_rotation(video_path)
                    
                    # 如果元数据中有旋转信息，优先使用
                    if metadata_rotation != 0:
                        rotation = metadata_rotation
                        logger.info(f"使用元数据中的旋转信息: {rotation}°")
                    
                    logger.info(f"视频旋转角度: {rotation}°")
                    
                    # 考虑旋转后的实际方向
                    effective_width, effective_height = v_width, v_height
                    if rotation in [90, 270, -90]:
                        effective_width, effective_height = v_height, v_width
                    
                    # 判断视频方向
                    is_portrait = effective_height > effective_width
                    target_is_portrait = video_height > video_width
                    
                    # 检查视频方向是否与目标方向不符
                    if is_portrait != target_is_portrait and rotation == 0:
                        # 只有当确实需要转为竖屏时才旋转 (针对竖屏拍摄但元数据错误的视频)
                        if not is_portrait and target_is_portrait:
                            # 竖屏拍摄但元数据显示为横屏的特殊情况
                            aspect_ratio = v_width / v_height if v_height > 0 else 0
                            if 1.7 < aspect_ratio < 1.8:  # 接近16:9
                                logger.info("检测到可能是竖屏拍摄但元数据标记为横屏(16:9)，添加90度旋转")
                                rotation = 90
                            else:
                                logger.info(f"视频方向({is_portrait})与目标方向({target_is_portrait})不同，但不需要旋转")
                        else:
                            logger.info(f"视频方向({is_portrait})与目标方向({target_is_portrait})不同，但不需要旋转")
                    
                    # 确定每个片段的时长
                    clip_duration = min(max_clip_duration, v_duration)
                    
                    # 如果是顺序模式，只取一个片段
                    if video_concat_mode == VideoConcatMode.sequential:
                        start_times = [0]
                    else:
                        # 如果是随机模式，尝试取多个片段
                        start_times = []
                        if v_duration > max_clip_duration:
                            # 计算可以取多少个不重叠的片段
                            num_clips = min(3, int(v_duration / max_clip_duration))
                            for i in range(num_clips):
                                start_time = i * max_clip_duration
                                if start_time + max_clip_duration <= v_duration:
                                    start_times.append(start_time)
                        else:
                            start_times = [0]
                    
                    for start_time in start_times:
                        segment_filename = f"segment_{segment_index:03d}.mp4"
                        segment_path = os.path.join(temp_dir, segment_filename)
                        segment_index += 1
                        
                        # 计算片段时长
                        segment_duration = min(max_clip_duration, v_duration - start_time)
                        
                        # 构建旋转滤镜
                        rotate_filter = ""
                        if rotation == 90:
                            rotate_filter = "transpose=1,"  # 顺时针旋转90度
                            logger.info("应用90度顺时针旋转")
                        elif rotation == 180:
                            rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
                            logger.info("应用180度旋转")
                        elif rotation == 270 or rotation == -90:
                            rotate_filter = "transpose=2,"  # 逆时针旋转90度
                            logger.info("应用270度旋转")
                        
                        # 获取编码器参数
                        hw_accel = ""
                        hw_accel_cmd = ["ffmpeg", "-hide_banner", "-encoders"]
                        hw_encoders = subprocess.check_output(hw_accel_cmd, universal_newlines=True)
                        
                        if "h264_nvenc" in hw_encoders:
                            hw_accel = "h264_nvenc"
                            logger.info("使用NVIDIA硬件加速编码")
                        elif "h264_qsv" in hw_encoders:
                            hw_accel = "h264_qsv"
                            logger.info("使用Intel硬件加速编码")
                        elif "h264_amf" in hw_encoders:
                            hw_accel = "h264_amf"
                            logger.info("使用AMD硬件加速编码")
                        else:
                            hw_accel = "libx264"
                            logger.info("使用软件编码器")
                        
                        # 获取编码器优化参数
                        encoder_params = EncoderConfig.get_encoder_params(hw_accel, v_width, v_height)
                        encoder_args = []
                        for key, value in encoder_params.items():
                            if key not in ["bitrate", "maxrate", "bufsize"]:
                                encoder_args.extend([f"-{key}", str(value)])
                        
                        # 设置码率参数
                        bitrate = encoder_params["bitrate"]
                        maxrate = encoder_params["maxrate"]
                        bufsize = encoder_params["bufsize"]
                        
                        # 根据视频方向和目标方向设置缩放参数
                        scale_filter = ""
                        if is_portrait:
                            # 竖屏视频
                            if aspect == VideoAspect.portrait:
                                # 目标也是竖屏，保持比例并使用高质量缩放
                                scale_filter = "scale=1080:-2:flags=lanczos+accurate_rnd"
                            else:
                                # 目标是横屏，需要确保不裁剪内容
                                scale_filter = "scale=-2:1080:flags=lanczos+accurate_rnd"
                        else:
                            # 横屏视频
                            if aspect == VideoAspect.landscape:
                                # 目标也是横屏，保持比例
                                scale_filter = "scale=1920:-2:flags=lanczos+accurate_rnd"
                            else:
                                # 目标是竖屏，需要确保不裁剪内容
                                scale_filter = "scale=-2:1920:flags=lanczos+accurate_rnd"
                        
                        # 专门针对4K HEVC视频的特殊处理
                        if codec.lower() == 'hevc' and (v_width >= 3840 or v_height >= 3840):
                            # 计算宽高比判断原始方向
                            aspect_ratio = v_width / v_height
                            is_standard_landscape = 1.7 < aspect_ratio < 1.8
                            
                            if is_standard_landscape and rotation == 0:
                                # 明确是标准横屏4K视频，保持横屏方向
                                logger.info("检测到标准横屏4K HEVC视频，保持原始横屏方向")
                                # 如果仅需要转码但不需要旋转，只调整分辨率
                                target_width = min(v_width, 1920)
                                target_height = int(target_width / aspect_ratio)
                                rotate_filter = ""  # 禁用旋转
                        
                        # 处理HEVC编码的视频
                        if codec.lower() == 'hevc':
                            # 先进行转码处理
                            hevc_output = os.path.join(temp_dir, f"hevc_converted_{segment_index:03d}.mp4")
                            
                            # 使用更精确的处理参数
                            hevc_cmd = [
                                "ffmpeg", "-y",
                                "-ss", str(start_time),
                                "-i", video_path,
                                "-t", str(segment_duration),
                                "-map_metadata", "-1",  # 移除所有元数据
                                "-vf", f"{rotate_filter}{scale_filter},format=yuv420p",
                                "-c:v", hw_accel,
                                "-b:v", f"{bitrate}k",
                                "-maxrate", f"{maxrate}k",
                                "-bufsize", f"{bufsize}k",
                            ]
                            
                            # 只为视频添加编码器参数
                            for key, value in encoder_params.items():
                                if key not in ["bitrate", "maxrate", "bufsize"]:
                                    if key.startswith("profile") or key.startswith("level"):
                                        # 只为视频流添加profile和level参数
                                        hevc_cmd.extend([f"-{key}", str(value)])
                                    elif not key.startswith("profile") and not key.startswith("level"):
                                        # 其他非profile/level参数直接添加
                                        hevc_cmd.extend([f"-{key}", str(value)])
                            
                            # 添加其他通用参数
                            hevc_cmd.extend([
                                "-pix_fmt", "yuv420p",
                                "-color_primaries", "bt709",
                                "-color_trc", "bt709",
                                "-colorspace", "bt709",
                                "-movflags", "+faststart",
                                "-an",  # 不包含音频
                                "-max_muxing_queue_size", "9999",
                                hevc_output
                            ])
                            
                            logger.info(f"处理HEVC视频: {os.path.basename(video_path)}, 从{start_time}秒开始, 时长{segment_duration}秒")
                            hevc_result = subprocess.run(hevc_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                            
                            if hevc_result.returncode != 0:
                                logger.error(f"HEVC转码失败: {hevc_result.stderr}")
                                continue
                                
                            # 确认转码后的视频
                            if not os.path.exists(hevc_output) or os.path.getsize(hevc_output) == 0:
                                logger.error("HEVC转码输出文件无效")
                                continue
                                
                            # 复制转码后的视频到最终片段
                            copy_cmd = [
                                "ffmpeg", "-y",
                                "-i", hevc_output,
                                "-c", "copy",  # 直接复制，不重新编码
                                segment_path
                            ]
                            
                            copy_result = subprocess.run(copy_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                            if copy_result.returncode != 0:
                                logger.error(f"复制HEVC处理结果失败: {copy_result.stderr}")
                                continue
                        else:
                            # 普通视频直接处理
                            process_cmd = [
                                "ffmpeg", "-y",
                                "-ss", str(start_time),
                                "-i", video_path,
                                "-t", str(segment_duration),
                                "-vf", f"{rotate_filter}{scale_filter},format=yuv420p",
                                "-an",  # 去除音频
                                "-c:v", hw_accel,
                                "-b:v", f"{bitrate}k",
                                "-maxrate", f"{maxrate}k",
                                "-bufsize", f"{bufsize}k",
                            ]
                            
                            # 只为视频添加编码器参数
                            for key, value in encoder_params.items():
                                if key not in ["bitrate", "maxrate", "bufsize"]:
                                    if key.startswith("profile") or key.startswith("level"):
                                        # 只为视频流添加profile和level参数
                                        process_cmd.extend([f"-{key}", str(value)])
                                    elif not key.startswith("profile") and not key.startswith("level"):
                                        # 其他非profile/level参数直接添加
                                        process_cmd.extend([f"-{key}", str(value)])
                            
                            # 添加其他通用参数
                            process_cmd.extend([
                                "-map_metadata", "-1",  # 移除所有元数据
                                segment_path
                            ])
                            
                            logger.info(f"处理普通视频片段: {os.path.basename(video_path)}, 从{start_time}秒开始, 时长{segment_duration}秒")
                            segment_result = subprocess.run(process_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                            
                            if segment_result.returncode != 0:
                                logger.error(f"创建片段失败: {segment_result.stderr}")
                                continue
                        
                        # 验证输出文件
                        if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                            processed_segments.append({
                                "file": segment_path,
                                "duration": segment_duration
                            })
                            segment_files.append(segment_path)
                            logger.success(f"片段创建成功: {segment_filename}")
                        else:
                            logger.error(f"创建的片段无效: {segment_path}")
                
                except Exception as e:
                    logger.error(f"处理视频时出错: {str(e)}", exc_info=True)
                    continue
                    
            except Exception as e:
                logger.error(f"处理视频失败: {os.path.basename(video_path)}, 错误: {str(e)}")
                continue
        
        # 如果没有有效片段，返回None
        if not processed_segments:
            logger.error("没有有效的视频片段，合成失败")
            return None
        
        # 根据需要打乱片段顺序
        if video_concat_mode.value == VideoConcatMode.random.value:
            random.shuffle(processed_segments)
        
        # 计算所有片段的总时长
        total_segment_duration = sum(segment["duration"] for segment in processed_segments)
        
        # 如果总时长不足音频时长，则循环使用片段
        if total_segment_duration < audio_duration:
            original_segments = processed_segments.copy()
            while total_segment_duration < audio_duration:
                # 复制一份片段列表并打乱（如果是随机模式）
                additional_segments = original_segments.copy()
                if video_concat_mode.value == VideoConcatMode.random.value:
                    random.shuffle(additional_segments)
                    
                # 添加片段，直到时长足够
                for segment in additional_segments:
                    processed_segments.append(segment)
                    total_segment_duration += segment["duration"]
                    if total_segment_duration >= audio_duration:
                        break
        
        # 创建concat文件
        concat_file = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_file, "w") as f:
            for segment in processed_segments:
                f.write(f"file '{segment['file']}'\n")
        
        # 合并视频片段
        merged_video = os.path.join(temp_dir, "merged_video.mp4")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            merged_video
        ]
        
        logger.info("合并视频片段...")
        concat_result = subprocess.run(concat_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        if concat_result.returncode != 0:
            logger.error(f"合并视频片段失败: {concat_result.stderr}")
            return None
        
        if not os.path.exists(merged_video) or os.path.getsize(merged_video) == 0:
            logger.error("合并视频片段失败")
            return None
        
        # 将音频添加到视频
        final_cmd = [
            "ffmpeg", "-y",
            "-i", merged_video,
            "-i", audio_file,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",  # 使用最短的输入时长
            combined_video_path
        ]
        
        logger.info("添加音频到视频...")
        final_result = subprocess.run(final_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        if final_result.returncode != 0:
            logger.error(f"添加音频失败: {final_result.stderr}")
            return None
        
        # 检查最终输出
        if os.path.exists(combined_video_path) and os.path.getsize(combined_video_path) > 0:
            logger.success(f"视频合成成功: {os.path.basename(combined_video_path)}")
            return combined_video_path
        else:
            logger.error("视频合成失败")
            return None
            
    except Exception as e:
        logger.error(f"视频合成过程中出错: {str(e)}")
        return None
    finally:
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.error(f"清理临时文件失败: {str(e)}")

def test_rotation_detection(video_path: str):
    """测试旋转检测函数的各种方法"""
    try:
        logger.info(f"测试视频旋转检测: {video_path}")
        
        # 使用常规方法
        rotation = get_video_rotation(video_path)
        logger.info(f"检测到的旋转角度: {rotation}°")
        
        # 使用mediainfo（如果可用）
        try:
            mediainfo_cmd = ["mediainfo", "--Output=JSON", video_path]
            mediainfo_result = subprocess.run(mediainfo_cmd, capture_output=True, encoding='utf-8', errors='replace')
            if mediainfo_result.returncode == 0:
                logger.info("Mediainfo 可用")
                mediainfo_data = json.loads(mediainfo_result.stdout)
                for track in mediainfo_data.get("media", {}).get("track", []):
                    if track.get("@type") == "Video" and "Rotation" in track:
                        rotation = int(float(track["Rotation"]))
                        logger.info(f"Mediainfo 旋转值: {rotation}°")
            else:
                logger.info("Mediainfo 不可用")
        except Exception as e:
            logger.info(f"Mediainfo 测试失败: {str(e)}")
        
        # 使用ffmpeg
        try:
            cmd = [
                "ffmpeg",
                "-i", video_path,
                "-hide_banner"
            ]
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            if result.returncode != 0 and "rotation" in result.stderr.lower():
                logger.info("发现旋转信息在ffmpeg输出中")
                # 尝试提取旋转信息
                rotation_patterns = [r'rotate\s*:\s*(\d+)', r'rotation\s*:\s*(\d+)']
                for pattern in rotation_patterns:
                    matches = re.search(pattern, result.stderr, re.IGNORECASE)
                    if matches:
                        rotation = int(matches.group(1))
                        logger.info(f"FFmpeg 旋转值: {rotation}°")
                        break
        except Exception as e:
            logger.info(f"FFmpeg 测试失败: {str(e)}")
        
        # 总结
        logger.info(f"最终旋转角度检测: {get_video_rotation(video_path)}°")
        return get_video_rotation(video_path)
    except Exception as e:
        logger.error(f"测试失败: {str(e)}")
        return 0


class EncoderConfig:
    """编码器配置类"""
    @staticmethod
    def get_optimal_bitrate(width: int, height: int, is_4k: bool = False) -> int:
        """计算最优码率（kbps）"""
        pixel_count = width * height
        if is_4k:
            # 4K视频使用更高的码率基准
            base_bitrate = 24000  # 24Mbps基准 (提高到原来的15Mbps的1.6倍)
        else:
            # 1080p及以下分辨率
            base_bitrate = 12000   # 12Mbps基准 (提高到原来的8Mbps的1.5倍)
            
        # 根据像素数调整码率
        bitrate = int((pixel_count / (1920 * 1080)) * base_bitrate)
        
        # 确保码率在合理范围内
        return max(6000, min(bitrate, 30000))  # 提高最低和最高码率限制
    
    @staticmethod
    def get_encoder_params(hw_accel: str, width: int, height: int) -> dict:
        """获取编码器参数"""
        is_4k = width * height >= 3840 * 2160
        bitrate = EncoderConfig.get_optimal_bitrate(width, height, is_4k)
        
        # 基础参数
        params = {
            "bitrate": bitrate,
            "maxrate": int(bitrate * 1.5),
            "bufsize": int(bitrate * 3),  # 增大缓冲区
            "refs": 5 if is_4k else 4,    # 增加参考帧
            "g": 30,  # GOP大小
        }
        
        # 根据不同硬件加速器优化参数
        if hw_accel == "h264_nvenc":
            params.update({
                "preset": "p7",  # 最高质量 (从p4提高到p7)
                "rc": "vbr_hq",  # 高质量可变码率
                "cq": 16,        # 降低质量参数以提高画质 (从19降到16)
                "profile:v": "high",  # 修正：使用profile:v而不是profile
                "level:v": "4.2",     # 修正：为视频指定level
                "spatial-aq": "1",    # 空间自适应量化
                "temporal-aq": "1",   # 时间自适应量化
                "b_ref_mode": "each", # 每一帧作为参考 (从middle提高到each)
                "rc-lookahead": "60"  # 增加前瞻帧数
            })
        elif hw_accel == "h264_qsv":
            params.update({
                "preset": "veryslow", # 最慢压缩 = 最高质量
                "look_ahead": "1",
                "global_quality": 18, # 降低参数以提高质量 (从23降到18)
                "profile:v": "high",  # 修正：使用profile:v而不是profile
                "level:v": "4.1"      # 修正：为视频指定level
            })
        elif hw_accel == "h264_amf":
            params.update({
                "quality": "quality",
                "profile:v": "high",  # 修正：使用profile:v而不是profile
                "level:v": "4.1",     # 修正：为视频指定level
                "refs": 5,  # 增加参考帧
                "preanalysis": "1",
                "vbaq": "1"  # 启用方差基础自适应量化
            })
        else:  # libx264
            params.update({
                "preset": "slow",   # 保持slow，平衡速度和质量
                "crf": "16",        # 降低CRF值以提高质量 (从18降到16)
                "profile:v": "high",  # 修正：使用profile:v而不是profile
                "level:v": "4.1",     # 修正：为视频指定level
                "x264opts": "rc-lookahead=60:ref=5:deblock=-1,-1:psy-rd=1.0:aq-strength=0.8:aq-mode=3" # 更复杂的优化参数
            })
        
        return params

def preprocess_video_ffmpeg(materials: List[MaterialInfo], clip_duration=4):
    """
    使用ffmpeg预处理视频和图片素材，全部转换为视频
    
    Args:
        materials: 素材信息列表
        clip_duration: 图片转视频的持续时间（秒）
    
    Returns:
        处理后的素材列表
    """
    for material in materials:
        if not material.url:
            continue

        ext = utils.parse_extension(material.url)
        
        # 先检查文件是否真的存在
        if not os.path.exists(material.url):
            logger.error(f"文件不存在: {material.url}")
            continue
        
        try:
            if ext in const.FILE_TYPE_VIDEOS:
                # 使用ffprobe获取视频信息
                probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", 
                            "-show_entries", "stream=width,height,codec_name,rotation", "-of", "json", material.url]
                try:
                    result = subprocess.run(probe_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                except Exception as e:
                    logger.error(f"获取视频信息失败: {str(e)}")
                    continue
                
                if result.returncode == 0:
                    try:
                        data = json.loads(result.stdout)
                    except json.JSONDecodeError as e:
                        logger.error(f"解析视频信息JSON失败: {str(e)}")
                        continue
                        
                    if "streams" in data and data["streams"]:
                        stream = data["streams"][0]
                        width = stream.get("width", 0)
                        height = stream.get("height", 0)
                        codec = stream.get("codec_name", "").lower()
                        
                        # 使用增强的旋转检测函数而不是直接从tags获取
                        rotation = get_video_rotation(material.url)
                        logger.info(f"视频信息: 宽={width}, 高={height}, 编码={codec}, 旋转={rotation}°")
                        
                        # 判断视频是否需要处理
                        needs_processing = False
                        
                        # 1. 尺寸太小的视频跳过
                        if width < 480 or height < 480:
                            logger.warning(f"视频太小，宽: {width}, 高: {height}")
                            continue
                        
                        # 2. 只有非H264视频才需要转码
                        if "h264" not in codec:
                            logger.info(f"非H264编码视频，需要转码: {codec}")
                            needs_processing = True
                        
                        # 3. 处理旋转情况
                        if rotation in [90, 180, 270]:
                            logger.info(f"视频需要旋转: {rotation}°")
                            needs_processing = True
                        
                        # 4. 处理分辨率
                        max_portrait_width = 1080
                        max_portrait_height = 1920
                        max_landscape_width = 1920
                        max_landscape_height = 1080
                        
                        # 考虑旋转后的尺寸
                        effective_width = width
                        effective_height = height
                        if rotation in [90, 270]:
                            effective_width, effective_height = height, width
                        
                        # 根据比例判断是横屏还是竖屏
                        is_portrait = effective_height > effective_width
                        
                        # 检查是否超过最大分辨率
                        if is_portrait and (effective_width > max_portrait_width or effective_height > max_portrait_height):
                            logger.info(f"竖屏视频尺寸超过限制: {effective_width}x{effective_height}")
                            needs_processing = True
                        elif not is_portrait and (effective_width > max_landscape_width or effective_height > max_landscape_height):
                            logger.info(f"横屏视频尺寸超过限制: {effective_width}x{effective_height}")
                            needs_processing = True
                        
                        if needs_processing:
                            logger.info(f"需要处理的视频: {material.url}")
                            output_path = os.path.join(os.path.dirname(material.url), f"processed_{os.path.basename(material.url)}")
                            
                            # 设置旋转滤镜
                            rotate_filter = ""
                            if rotation == 90:
                                rotate_filter = "transpose=1,"  # 顺时针旋转90度
                                logger.info("应用90度顺时针旋转滤镜")
                            elif rotation == 180:
                                rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
                                logger.info("应用180度旋转滤镜")
                            elif rotation == 270 or rotation == -90:
                                rotate_filter = "transpose=2,"  # 逆时针旋转90度（等于顺时针旋转270度）
                                logger.info("应用270度顺时针旋转滤镜")
                            
                            # 特殊情况处理：仅当竖屏拍摄但元数据显示为横屏时才旋转
                            needs_rotation_fix = False
                            if not is_portrait and rotation == 0:
                                # 检查宽高比是否接近16:9
                                aspect_ratio = width / height if height > 0 else 0
                                if 1.7 < aspect_ratio < 1.8:  # 接近16:9
                                    logger.info("检测到可能是竖屏拍摄但元数据标记为横屏(16:9)，添加90度旋转")
                                    rotate_filter = "transpose=1,"
                                    # 交换宽高
                                    width, height = height, width
                                    needs_rotation_fix = True
                                else:
                                    logger.info("横屏视频保持原始方向，不进行旋转")
                            
                            # 设置输出分辨率
                            target_width = width
                            target_height = height
                            
                            # 检查是否需要缩小视频
                            if width > 0 and height > 0:
                                if width > height:  # 横屏视频
                                    if width > max_landscape_width:
                                        scale_ratio = max_landscape_width / width
                                        target_width = max_landscape_width
                                        target_height = int(height * scale_ratio)
                                else:  # 竖屏视频
                                    if height > max_portrait_height:
                                        scale_ratio = max_portrait_height / height
                                        target_height = max_portrait_height
                                        target_width = int(width * scale_ratio)
                            
                            scale_filter = f"scale={target_width}:{target_height}:flags=lanczos+accurate_rnd"
                            
                            # 构建完整的视频滤镜
                            vf_filter = ""
                            if rotate_filter and scale_filter:
                                vf_filter = f"{rotate_filter}{scale_filter},format=yuv420p"
                            elif rotate_filter:
                                vf_filter = f"{rotate_filter}format=yuv420p"
                            elif scale_filter:
                                vf_filter = f"{scale_filter},format=yuv420p"
                            else:
                                vf_filter = "format=yuv420p"
                            
                            # 获取编码器参数
                            hw_accel = ""
                            hw_accel_cmd = ["ffmpeg", "-hide_banner", "-encoders"]
                            hw_encoders = subprocess.check_output(hw_accel_cmd, universal_newlines=True)
                            
                            if "h264_nvenc" in hw_encoders:
                                hw_accel = "h264_nvenc"
                                logger.info("使用NVIDIA硬件加速编码")
                            elif "h264_qsv" in hw_encoders:
                                hw_accel = "h264_qsv"
                                logger.info("使用Intel硬件加速编码")
                            elif "h264_amf" in hw_encoders:
                                hw_accel = "h264_amf"
                                logger.info("使用AMD硬件加速编码")
                            else:
                                hw_accel = "libx264"
                                logger.info("使用软件编码器")
                            
                            # 获取优化的编码参数
                            encoder_params = EncoderConfig.get_encoder_params(hw_accel, target_width, target_height)
                            logger.info(f"编码器参数: {encoder_params}")
                            
                            # 构建编码器参数字符串
                            encoder_args = []
                            for key, value in encoder_params.items():
                                if key not in ["bitrate", "maxrate", "bufsize"]:
                                    encoder_args.extend([f"-{key}", str(value)])
                            
                            # 设置码率参数
                            bitrate = encoder_params["bitrate"]
                            maxrate = encoder_params["maxrate"]
                            bufsize = encoder_params["bufsize"]
                            
                            # 转码命令
                            transcode_cmd = [
                                "ffmpeg", "-y",
                                "-i", material.url,
                                "-vf", vf_filter,
                                "-c:v", hw_accel,
                                "-b:v", f"{bitrate}k",
                                "-maxrate", f"{maxrate}k",
                                "-bufsize", f"{bufsize}k",
                                *encoder_args,
                                "-c:a", "aac",
                                "-b:a", "192k",
                                "-map_metadata", "-1",  # 移除所有元数据
                                "-movflags", "+faststart",  # 添加快速启动标志
                                "-max_muxing_queue_size", "9999",
                                output_path
                            ]
                            
                            # 执行命令并捕获错误
                            try:
                                process = subprocess.Popen(
                                    transcode_cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    universal_newlines=True,
                                    encoding='utf-8',
                                    errors='replace'
                                )
                                
                                # 收集错误输出
                                stderr_output = []
                                for line in process.stderr:
                                    stderr_output.append(line)
                                    # 显示进度信息
                                    if "time=" in line and "bitrate=" in line:
                                        logger.info(f"视频处理进度: {line.strip()}")
                                
                                process.wait()
                                
                                if process.returncode != 0:
                                    logger.error(f"视频处理失败，错误详情:")
                                    for line in stderr_output:
                                        logger.error(line.strip())
                                    continue
                            except Exception as e:
                                logger.error(f"执行转码命令失败: {str(e)}")
                                continue
                            
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                material.url = output_path
                                logger.info(f"视频处理成功: {output_path}")
                            else:
                                logger.error(f"视频处理失败: {material.url}")
                else:
                    logger.error(f"无法读取视频信息: {material.url}")
                    continue
            elif ext in const.FILE_TYPE_IMAGES:
                logger.info(f"处理图片: {material.url}")
                # 使用ffmpeg将图片转换为视频，添加缩放效果
                video_file = f"{material.url}.mp4"
                
                # 缩放效果：使用zoompan滤镜实现缩放效果
                image_cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1",  # 循环输入
                    "-i", material.url,
                    "-vf", f"zoompan=z='min(zoom+0.0015,1.2)':d={int(clip_duration*30)}:fps=30,format=yuv420p",
                    "-c:v", "libx264",
                    "-t", str(clip_duration),
                    "-pix_fmt", "yuv420p",
                    video_file
                ]
                
                subprocess.run(image_cmd, check=True, capture_output=True)
                
                if os.path.exists(video_file) and os.path.getsize(video_file) > 0:
                    material.url = video_file
                    logger.info(f"图片转视频成功: {video_file}")
                else:
                    logger.error(f"图片转视频失败: {material.url}")
            else:
                logger.warning(f"不支持的文件类型: {material.url}")
                continue
                
        except Exception as e:
            logger.error(f"处理素材失败: {material.url}, 错误: {str(e)}")
            continue
            
    return materials

# 保留preprocess_video名称，使用ffmpeg实现
def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
    """
    使用ffmpeg处理视频和图片素材
    """
    return preprocess_video_ffmpeg(materials, clip_duration)


if __name__ == "__main__":
    # 测试旋转检测
    import sys
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        print(f"测试文件: {test_file}")
        rotation = test_rotation_detection(test_file)
        print(f"检测到的旋转角度: {rotation}")
        codec = get_video_codec(test_file)
        print(f"检测到的编码: {codec}")
        sys.exit(0)
        
    # 原有的测试代码
    m = MaterialInfo()
    m.url = "/Users/harry/Downloads/IMG_2915.JPG"
    m.provider = "local"
    materials = preprocess_video([m], clip_duration=4)
    print(materials)
