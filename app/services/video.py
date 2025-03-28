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

# 导入视频元数据处理模块
from app.services.video_metadata import VideoMetadataExtractor

# 导入预处理模块
from app.services.preprocess_video import VideoPreprocessor


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

# 为了向后兼容，保留get_video_rotation函数，但是委托给VideoMetadataExtractor类
def get_video_rotation(video_path: str) -> int:
    """获取视频旋转元数据，支持多种格式的旋转信息"""
    return VideoMetadataExtractor.get_video_rotation(video_path)

# 同样保留get_video_codec函数，但是委托给VideoMetadataExtractor类
def get_video_codec(video_path: str) -> str:
    """获取视频编码格式和详细信息"""
    return VideoMetadataExtractor.get_video_codec(video_path)

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
    将多个视频合并成一个视频，并添加音频，使用纯ffmpeg实现。
    
    Args:
        combined_video_path: 合并后的视频路径
        video_paths: 视频路径列表
        audio_file: 音频文件路径
        video_aspect: 视频比例
        video_concat_mode: 视频连接模式（random随机、sequential顺序）
        video_transition_mode: 视频转场特效
        max_clip_duration: 最大片段时长（秒）
        threads: 线程数
        
    Returns:
        合并后的视频路径
    """
    # 调用纯ffmpeg实现
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
        audio_info = json.loads(subprocess.check_output(audio_probe_cmd, universal_newlines=True))
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
                    "-show_entries", "stream=width,height,r_frame_rate,duration,codec_name", 
                    "-of", "json", 
                    video_path
                ]
                
                info_result = subprocess.run(info_cmd, capture_output=True, text=True)
                
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
                        format_result = subprocess.run(format_cmd, capture_output=True, text=True)
                        try:
                            v_duration = float(format_result.stdout.strip())
                        except:
                            # 如果还是无法获取，则计算帧数/帧率
                            frames_cmd = ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", video_path]
                            frames_result = subprocess.run(frames_cmd, capture_output=True, text=True)
                            try:
                                frame_count_str = frames_result.stdout.strip()
                                # 解析帧数，需要处理可能存在的逗号
                                frame_count = int(frame_count_str.replace(',', ''))
                                v_duration = frame_count / fps
                            except:
                                logger.warning(f"无法计算视频时长，使用默认值10秒")
                                v_duration = 10.0
                    
                    logger.info(f"视频信息: {v_width}x{v_height}, {fps:.2f}fps, {v_duration:.2f}秒, 编码: {codec}")
                    
                    # 注意：由于我们在预处理阶段已经处理了视频旋转，这里不再需要获取或处理旋转信息
                    
                    # 判断视频方向
                    is_portrait = v_height > v_width
                    
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
                        
                        # 根据目标分辨率设置缩放参数
                        # 比较视频方向与目标方向，确定缩放方式
                        if (is_portrait and aspect == VideoAspect.portrait) or (not is_portrait and aspect == VideoAspect.landscape):
                            # 方向一致，保持比例缩放
                            scale_filter = f"scale={video_width}:{video_height}:force_original_aspect_ratio=1,crop={video_width}:{video_height}"
                        elif is_portrait and aspect == VideoAspect.landscape:
                            # 竖屏视频输出为横屏，裁剪中间区域
                            scale_filter = f"scale={video_height}:{video_width}:force_original_aspect_ratio=1,crop={video_width}:{video_height}"
                        else:
                            # 横屏视频输出为竖屏，裁剪中间区域
                            scale_filter = f"scale={video_height}:{video_width}:force_original_aspect_ratio=1,crop={video_width}:{video_height}"
                            
                        # 判断视频尺寸是否合适
                        if v_width < video_width or v_height < video_height:
                            logger.warning(f"视频尺寸({v_width}x{v_height})小于目标尺寸({video_width}x{video_height})，使用填充模式")
                            # 使用填充而非裁剪
                            scale_filter = f"scale='min({video_width},iw)':'min({video_height},ih)':force_original_aspect_ratio=1,pad={video_width}:{video_height}:(ow-iw)/2:(oh-ih)/2:color=black"
                        
                        # 构造安全的片段命令
                        segment_cmd = [
                            "ffmpeg", "-y",
                            "-ss", str(start_time),
                            "-i", video_path,
                            "-t", str(segment_duration),
                            "-vf", f"{scale_filter}",
                            "-c:v", "libx264",
                            "-preset", "fast",
                            "-crf", "23",
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-pix_fmt", "yuv420p",
                            segment_path
                        ]
                        
                        # 执行命令
                        try:
                            subprocess.run(segment_cmd, check=True, capture_output=True)
                        except subprocess.CalledProcessError as e:
                            logger.error(f"处理视频片段失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
                            continue
                        
                        if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                            segment_files.append(segment_path)
                            logger.info(f"创建视频片段: {segment_path}, 时长: {segment_duration:.2f}秒")
                        else:
                            logger.error(f"创建视频片段失败: {segment_path}")
                except Exception as e:
                    logger.error(f"处理视频失败: {str(e)}")
                    continue
            except Exception as e:
                logger.error(f"处理视频{video_path}时发生错误: {str(e)}")
                continue
        
        # 如果没有有效片段，返回失败
        if not segment_files:
            logger.error("没有有效的视频片段")
            return None
            
        # 随机打乱片段顺序（如果是随机模式）
        if video_concat_mode == VideoConcatMode.random:
            random.shuffle(segment_files)
        
        # 创建片段列表文件
        segments_list_path = os.path.join(temp_dir, "segments.txt")
        with open(segments_list_path, "w") as f:
            for segment in segment_files:
                f.write(f"file '{segment}'\n")
        
        # 使用concat分离器合并视频片段
        concat_output_path = os.path.join(temp_dir, "concat_output.mp4")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", segments_list_path,
            "-c", "copy",
            concat_output_path
        ]
        
        try:
            subprocess.run(concat_cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"合并视频片段失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
            return None
        
        # 添加音频
        final_output_cmd = [
            "ffmpeg", "-y",
            "-i", concat_output_path,
            "-i", audio_file,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            combined_video_path
        ]
        
        try:
            subprocess.run(final_output_cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"添加音频失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
            return None
        
        if os.path.exists(combined_video_path) and os.path.getsize(combined_video_path) > 0:
            logger.info(f"视频合成成功: {combined_video_path}")
            return combined_video_path
        else:
            logger.error("最终视频合成失败")
            return None
    except Exception as e:
        logger.error(f"视频合成过程中发生错误: {str(e)}")
        return None
    finally:
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"清理临时目录失败: {str(e)}")


# 删除原有的VideoMetadataHandler类，使用新的VideoMetadataExtractor类
# 为了向后兼容，保留一个空的VideoMetadataHandler类，继承自VideoMetadataExtractor
class VideoMetadataHandler(VideoMetadataExtractor):
    """向后兼容的VideoMetadataHandler类，实际使用VideoMetadataExtractor的功能"""
    pass


def create_fallback_video(combined_video_path: str, audio_file: str, duration: float = 24.0) -> str:
    """创建一个静态视频作为失败时的备选方案"""
    try:
        # 创建黑色背景视频
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s=1080x1920:d={duration}",
            "-i", audio_file,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            combined_video_path
        ]
        subprocess.run(cmd, check=True)
        return combined_video_path
    except Exception as e:
        logger.error(f"创建备选视频失败: {str(e)}")
        return None


def generate_video(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    """
    使用纯ffmpeg生成视频
    
    Args:
        video_path: 视频文件路径
        audio_path: 音频文件路径
        subtitle_path: 字幕文件路径
        output_file: 输出文件路径
        params: 视频参数
    
    Returns:
        生成的视频文件路径或None
    """
    return generate_video_ffmpeg(
        video_path=video_path,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        output_file=output_file,
        params=params
    )


def generate_video_ffmpeg(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    """使用纯ffmpeg实现视频生成，完全不依赖MoviePy"""
    try:
        logger.info("使用ffmpeg生成视频")
        temp_dir = os.path.dirname(output_file)
        
        # 获取目标分辨率
        if params.video_aspect == VideoAspect.portrait:
            max_width = 1080
            max_height = 1920
        else:
            max_width = 1920
            max_height = 1080
        
        logger.info(f"最大视频尺寸: {max_width} x {max_height}")
        logger.info(f"视频: {video_path}")
        logger.info(f"音频: {audio_path}")
        logger.info(f"字幕: {subtitle_path}")
        logger.info(f"输出: {output_file}")
        
        # 检查硬件加速支持
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
        
        # 获取视频信息
        probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", 
                    "-show_entries", "stream=width,height,codec_name", "-of", "json", video_path]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        
        if probe_result.returncode != 0:
            logger.error(f"无法获取视频信息: {probe_result.stderr}")
            return None
        
        video_info = json.loads(probe_result.stdout)
        
        if "streams" not in video_info or not video_info["streams"]:
            logger.error("无法读取视频流信息")
            return None
        
        stream = video_info["streams"][0]
        original_width = int(stream.get("width", 0))
        original_height = int(stream.get("height", 0))
        original_codec = stream.get("codec_name", "")
                
        logger.info(f"原始视频信息 - 宽: {original_width}, 高: {original_height}, 编码: {original_codec}")
        
        # 处理视频分辨率 - 预处理应该已经处理了旋转问题，这里只关注分辨率
        processed_video = os.path.join(temp_dir, "processed_video.mp4")
        
        # 设置尺寸调整滤镜
        scale_filter = ""
        needs_resize = False
        
        # 检查是否需要调整尺寸
        if original_width > max_width or original_height > max_height:
            needs_resize = True
            logger.info(f"视频尺寸超过限制，需要调整")
            
            # 计算调整后的尺寸，保持宽高比
            if params.video_aspect == VideoAspect.portrait:
                # 目标是竖屏视频
                if original_height > original_width:
                    # 原始也是竖屏，保持比例
                    scale_ratio = min(max_width / original_width, max_height / original_height)
                    target_width = int(original_width * scale_ratio)
                    target_height = int(original_height * scale_ratio)
                    scale_filter = f"scale={target_width}:{target_height}:flags=lanczos+accurate_rnd"
                else:
                    # 原始是横屏，需要居中放置在竖屏框架中
                    scale_ratio = min(max_width / original_width, max_height / original_height)
                    target_width = int(original_width * scale_ratio)
                    target_height = int(original_height * scale_ratio)
                    scale_filter = f"scale={target_width}:{target_height}:flags=lanczos+accurate_rnd,pad={max_width}:{max_height}:(ow-iw)/2:(oh-ih)/2"
            else:
                # 目标是横屏视频
                if original_width > original_height:
                    # 原始也是横屏，保持比例
                    scale_ratio = min(max_width / original_width, max_height / original_height)
                    target_width = int(original_width * scale_ratio)
                    target_height = int(original_height * scale_ratio)
                    scale_filter = f"scale={target_width}:{target_height}:flags=lanczos+accurate_rnd"
                else:
                    # 原始是竖屏，需要居中放置在横屏框架中
                    scale_ratio = min(max_width / original_width, max_height / original_height)
                    target_width = int(original_width * scale_ratio)
                    target_height = int(original_height * scale_ratio)
                    scale_filter = f"scale={target_width}:{target_height}:flags=lanczos+accurate_rnd,pad={max_width}:{max_height}:(ow-iw)/2:(oh-ih)/2"
        else:
            logger.info("视频尺寸在限制范围内，保持原始尺寸")
        
        # 编码参数设置
        is_4k = original_width * original_height >= 3840 * 2160
        is_hevc = original_codec.lower() == 'hevc'
        is_4k_hevc = is_4k and is_hevc
        
        # 获取优化的编码参数
        encoder_params = EncoderConfig.get_encoder_params(hw_accel, max_width, max_height)
        logger.info(f"编码器参数: {encoder_params}")
        
        # 提取编码参数
        bitrate = encoder_params["bitrate"]
        maxrate = encoder_params["maxrate"]
        bufsize = encoder_params["bufsize"]
        
        # 获取编码器参数列表
        encoder_args = []
        for key, value in encoder_params.items():
            if key not in ["bitrate", "maxrate", "bufsize"]:
                encoder_args.extend([f"-{key}", str(value)])
        
        # 确定是否需要处理视频
        needs_processing = needs_resize or original_codec != "h264"
        
        # 4K HEVC视频特殊处理：保持高质量
        if is_4k_hevc:
            # 增加码率以保持4K视频质量
            bitrate = min(20000, int(bitrate * 1.5))  # 提高码率，最高20Mbps
            maxrate = min(30000, int(maxrate * 1.5))  # 提高最大码率，最高30Mbps
            bufsize = min(40000, int(bufsize * 1.5))  # 提高缓冲大小
            logger.info(f"检测到4K HEVC视频，提高码率: {bitrate}k, 最大码率: {maxrate}k")
        
        if needs_processing:
            # 构建完整的视频滤镜
            full_filter = scale_filter
            
            # 添加像素格式转换确保兼容性
            if full_filter:
                full_filter += ",format=yuv420p"
            else:
                full_filter = "format=yuv420p"
            
            # 构建视频处理命令
            video_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", full_filter,
                "-c:v", hw_accel,
                "-b:v", f"{bitrate}k",
                "-maxrate", f"{maxrate}k",
                "-bufsize", f"{bufsize}k",
                *encoder_args,  # 展开其他编码器参数
                "-an",  # 不包含音频
                "-map_metadata", "-1",  # 移除所有元数据
                processed_video
            ]
            
            logger.info(f"处理视频中... 应用滤镜: {full_filter}")
            video_process = subprocess.Popen(
                video_cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                universal_newlines=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 收集完整错误输出
            stderr_output = []
            
            # 显示处理进度
            for line in video_process.stderr:
                stderr_output.append(line)
                if "time=" in line and "bitrate=" in line:
                    logger.info(f"视频处理进度: {line.strip()}")
            
            video_process.wait()
            
            if video_process.returncode != 0 or not os.path.exists(processed_video):
                logger.error("视频处理失败，错误详情:")
                for line in stderr_output:
                    logger.error(line.strip())
                return None
        else:
            # 直接复制视频流，不做任何处理
            logger.info("视频无需处理，直接使用原始视频")
            video_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-c:v", "copy",
                "-an",  # 不包含音频
                "-map_metadata", "-1",  # 移除所有元数据
                processed_video
            ]
            subprocess.run(video_cmd, check=True, capture_output=True)
            
            if not os.path.exists(processed_video):
                logger.error("视频处理失败")
                return None

        # 获取音频信息
        audio_probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_path]
        audio_info = json.loads(subprocess.check_output(audio_probe_cmd, universal_newlines=True))
        audio_duration = float(audio_info["format"]["duration"])
        
        # 处理背景音乐
        bgm_file = get_bgm_file(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
        processed_bgm = None
        
        if bgm_file and os.path.exists(bgm_file):
            processed_bgm = os.path.join(temp_dir, "processed_bgm.mp3")
            bgm_cmd = [
                "ffmpeg", "-y",
                "-i", bgm_file,
                "-af", f"volume={params.bgm_volume},afade=t=out:st={audio_duration-3}:d=3",
                "-to", str(audio_duration),
                processed_bgm
            ]
            
            logger.info("处理背景音乐...")
            subprocess.run(bgm_cmd, check=True, capture_output=True)
            
            if not os.path.exists(processed_bgm):
                logger.error("背景音乐处理失败")
                processed_bgm = None
        
        # 处理字幕
        subtitle_filter = ""
        if subtitle_path and os.path.exists(subtitle_path) and params.subtitle_enabled:
            # 准备字体
            font_path = ""
            if not params.font_name:
                params.font_name = "STHeitiMedium.ttc"
            font_path = os.path.join(utils.font_dir(), params.font_name)
            if os.name == "nt":
                font_path = font_path.replace("\\", "/")
                
            # 确定字幕位置
            alignment = 2  # 默认底部居中
            if params.subtitle_position == "top":
                alignment = 8  # 顶部居中
            elif params.subtitle_position == "center":
                alignment = 5  # 中间居中
                
            # 创建ASS字幕
            ass_subtitle = os.path.join(temp_dir, "subtitle.ass")
            subtitle_cmd = [
                "ffmpeg", "-y",
                "-i", subtitle_path,
                "-f", "ass",
                ass_subtitle
            ]
            
            logger.info("转换字幕格式...")
            subprocess.run(subtitle_cmd, check=True, capture_output=True, encoding='utf-8', errors='replace')
            
            if os.path.exists(ass_subtitle):
                try:
                    # 统一使用绝对路径+正斜杠
                    safe_subtitle_path = os.path.abspath(ass_subtitle).replace('\\', '/')
                    logger.debug(f"原始字幕路径: {ass_subtitle}")
                    logger.debug(f"处理后路径 (1): {safe_subtitle_path}")
                    
                    # Windows特殊处理
                    if os.name == "nt":
                        if ':' in safe_subtitle_path:
                            drive_part, path_part = safe_subtitle_path.split(':', 1)
                            # 使用原始字符串r来处理反斜杠，避免f-string语法错误
                            safe_subtitle_path = drive_part + r'\:' + path_part
                            logger.debug(f"Windows路径处理 (2): {safe_subtitle_path}")
                        
                        # 包裹在单引号中 - 确保FFmpeg正确解析路径
                        if not safe_subtitle_path.startswith("'") and not safe_subtitle_path.endswith("'"):
                            safe_subtitle_path = f"'{safe_subtitle_path}'"
                            logger.debug(f"添加引号 (3): {safe_subtitle_path}")
                    # 其他系统直接引用
                    else:
                        safe_subtitle_path = shlex.quote(safe_subtitle_path)
                        logger.debug(f"非Windows路径处理: {safe_subtitle_path}")
                    
                    # 确保字体名称安全
                    safe_font_name = params.font_name.replace(",", "\\,").replace(":", "\\:")
                    
                    # 计算垂直边距
                    vertical_margin = 50
                    # 构建字幕滤镜
                    subtitle_filter = f"subtitles={safe_subtitle_path}:force_style='FontName={safe_font_name},FontSize={params.font_size},PrimaryColour=&H{params.text_fore_color[1:]}&,OutlineColour=&H{params.stroke_color[1:]}&,BorderStyle=1,Outline={params.stroke_width},Alignment={alignment},MarginV={vertical_margin}'"

                    logger.info(f"字幕滤镜设置: {subtitle_filter}")
                except Exception as e:
                    logger.error(f"字幕路径处理失败: {str(e)}")
                    # 备选方案 - 简化处理，防止出错
                    try:
                        raw_path = ass_subtitle.replace('\\', '/')
                        if os.name == "nt" and ":" in raw_path:
                            # 最简单的处理方式
                            drive, rest = raw_path.split(":", 1)
                            raw_path = f"{drive}\\:{rest}"
                        # 计算垂直边距
                        vertical_margin = 50
                        # 构建字幕滤镜
                        subtitle_filter = f"subtitles='{raw_path}':force_style='FontName={params.font_name},FontSize={params.font_size},Alignment={alignment},MarginV={vertical_margin}'"
                        logger.info(f"使用备选字幕滤镜: {subtitle_filter}")
                    except Exception as e2:
                        logger.error(f"备选字幕处理也失败: {str(e2)}")
                        subtitle_filter = ""  # 失败时不添加字幕
                
                # 获取视频尺寸(用于日志记录和调试，不影响字幕处理)
                try:
                    # 使用JSON格式获取视频尺寸
                    json_cmd = [
                        "ffprobe",
                        "-v", "error",
                        "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "json",
                        processed_video
                    ]
                    json_result = subprocess.run(json_cmd, capture_output=True, encoding='utf-8', errors='replace', check=False).stdout
                    video_info = json.loads(json_result)
                    if "streams" in video_info and video_info["streams"]:
                        width = int(video_info["streams"][0].get("width", 1080))
                        height = int(video_info["streams"][0].get("height", 1920))
                        logger.info(f"视频尺寸: {width}x{height}")
                    else:
                        logger.warning("未找到视频流信息")
                except Exception as e:
                    logger.warning(f"获取视频尺寸失败: {str(e)}")
        
        # 音频处理
        merged_audio = os.path.join(temp_dir, "merged_audio.m4a")  # 修改扩展名为m4a而不是aac
        
        if processed_bgm:
            # 合并主音频和背景音乐
            audio_cmd = [
                "ffmpeg", "-y",
                "-i", audio_path,
                "-i", processed_bgm,
                "-filter_complex", f"[0:a]volume={params.voice_volume}[a1];[1:a]volume={params.bgm_volume}[a2];[a1][a2]amix=inputs=2:duration=longest[aout]",
                "-map", "[aout]",
                "-c:a", "aac",
                "-b:a", "192k",
                merged_audio
            ]
        else:
            # 只处理主音频
            audio_cmd = [
                "ffmpeg", "-y",
                "-i", audio_path,
                "-af", f"volume={params.voice_volume}",
                "-c:a", "aac",
                "-b:a", "192k",
                merged_audio
            ]
        
        logger.info("处理音频...")
        subprocess.run(audio_cmd, check=True, capture_output=True, encoding='utf-8', errors='replace')
        
        if not os.path.exists(merged_audio):
            logger.error("音频处理失败")
            return None
        
        # 最终合并视频、音频和字幕
        final_cmd = [
            "ffmpeg", "-y",
            "-i", processed_video,
            "-i", merged_audio
        ]
        
        # 生成最终视频... 命令构建
        logger.info("生成最终视频...")
        
        # 添加滤镜
        filter_complex = []
        
        if subtitle_filter:
            # 确保字幕滤镜格式正确，用单引号包围路径
            if "subtitles=" in subtitle_filter and not "subtitles='" in subtitle_filter:
                parts = subtitle_filter.split(':', 1)
                if len(parts) == 2:
                    path_part = parts[0]
                    rest_part = parts[1]
                    # 给路径加上单引号
                    path_part = path_part.replace("subtitles=", "subtitles='") + "'"
                    subtitle_filter = f"{path_part}:{rest_part}"
                    
            filter_complex.append(subtitle_filter)
            
        # 应用滤镜（如果有）
        if filter_complex:
            final_cmd.extend(["-vf", ",".join(filter_complex)])
        
        # 输出参数
        final_cmd.extend([
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", hw_accel
        ])
        
        # 根据编码器添加特定参数
        if hw_accel == "libx264":
            final_cmd.extend([
                "-preset", "medium",
                "-crf", "23"
            ])
        else:
            final_cmd.extend([
                "-preset", "p1"
            ])
        
        # 添加其他通用参数
        final_cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_file
        ])
        
        # 日志记录完整命令
        logger.info(f"生成最终视频... 命令: {' '.join(final_cmd)}")
        
        final_process = subprocess.Popen(
            final_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            universal_newlines=True,
            encoding='utf-8',
            errors='replace'
        )
        
        # 收集完整错误输出
        stderr_output = []
        
        # 显示处理进度
        for line in final_process.stderr:
            stderr_output.append(line)
            if "time=" in line and "bitrate=" in line:
                logger.info(f"最终合成进度: {line.strip()}")
        
        final_process.wait()
        
        if final_process.returncode != 0 or not os.path.exists(output_file):
            logger.error("最终视频生成失败，错误详情:")
            for line in stderr_output:
                logger.error(line.strip())
            return None
            
        logger.success(f"视频生成成功: {os.path.basename(output_file)}")
        return output_file
        
    except Exception as e:
        logger.error(f"视频生成过程中出错: {str(e)}")
        return None
    # finally:
    #     # 清理临时文件
    #     try:
    #         if 'temp_dir' in locals() and os.path.exists(temp_dir):
    #             logger.error(f"清理临时文件: {temp_dir}")
    #             shutil.rmtree(temp_dir)
    #     except Exception as e:
    #         logger.error(f"清理临时文件失败: {str(e)}")



# 保留preprocess_video名称，使用ffmpeg实现
def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
    """
    使用ffmpeg处理视频和图片素材
    """
    # 调用新模块的实现
    return VideoPreprocessor.preprocess_video(materials, clip_duration)

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
            
        # 获取视频详细信息
        try:
            info_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", 
                       "-show_entries", "stream=width,height,codec_name,display_aspect_ratio", 
                       "-of", "json", video_path]
            info_result = subprocess.run(info_cmd, capture_output=True, text=True)
            
            if info_result.returncode == 0:
                video_info = json.loads(info_result.stdout)
                if "streams" in video_info and video_info["streams"]:
                    stream = video_info["streams"][0]
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                    codec = stream.get("codec_name", "").lower()
                    aspect_ratio = stream.get("display_aspect_ratio", "")
                    
                    logger.info(f"视频信息 - 宽: {width}, 高: {height}, 编码: {codec}, 显示宽高比: {aspect_ratio}")
                    logger.info(f"视频方向: {'竖屏' if height > width else '横屏'}")
                    
                    # 计算实际宽高比
                    if height > 0:
                        actual_ratio = width / height
                        logger.info(f"实际宽高比: {actual_ratio:.4f}")
                        
                        # 判断是否接近16:9
                        if 1.7 < actual_ratio < 1.8:
                            logger.info("宽高比接近16:9")
                        # 判断是否接近9:16
                        elif 0.5 < actual_ratio < 0.6:
                            logger.info("宽高比接近9:16")
        except Exception as e:
            logger.info(f"获取视频信息失败: {str(e)}")
        
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
        
        # 超高清视频 (4K及以上)
        if is_4k:
            base_bitrate = 30000  # 30Mbps基准
        # 高清视频 (1080p-2K)
        elif pixel_count >= 1920 * 1080:
            base_bitrate = 15000  # 15Mbps基准
        # 标清视频
        else:
            base_bitrate = 8000   # 8Mbps基准
            
        # 根据像素数精确调整码率
        adjusted_bitrate = int((pixel_count / (1920 * 1080)) * base_bitrate)
        
        # 设置合理的下限和上限
        min_bitrate = 6000   # 最低保证6Mbps
        max_bitrate = 40000  # 最高不超过40Mbps
        
        # 确保码率在合理范围内
        return max(min_bitrate, min(adjusted_bitrate, max_bitrate))
    
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
            "refs": 6 if is_4k else 5,    # 增加参考帧数量
            "g": 30,  # GOP大小
        }
        
        # 根据不同硬件加速器优化参数
        if hw_accel == "h264_nvenc":
            params.update({
                "preset": "p7",    # 最高质量预设
                "tune": "hq",      # 高质量调优
                "rc": "vbr",       # 使用可变码率模式
                "cq": 15,          # 降低质量参数以提高画质
                "qmin": 10,        # 最小量化参数
                "qmax": 25,        # 最大量化参数
                "profile:v": "high",
                # 完全移除level参数，让NVENC自动选择合适的level
                "spatial-aq": "1", # 空间自适应量化
                "temporal-aq": "1", # 时间自适应量化
                "rc-lookahead": "32", # 前瞻帧数
                "surfaces": "32"    # 表面缓冲区
            })
        elif hw_accel == "h264_qsv":
            params.update({
                "preset": "veryslow", # 最慢压缩 = 最高质量
                "look_ahead": "1",
                "global_quality": 15, # 高质量参数
                "profile:v": "high"
                # 移除level参数
            })
        elif hw_accel == "h264_amf":
            params.update({
                "quality": "quality",
                "profile:v": "high",
                # 移除level参数
                "refs": 6 if is_4k else 5,  # 增加参考帧
                "preanalysis": "1",
                "vbaq": "1",  # 启用方差基础自适应量化
                "enforce_hrd": "1"
                # 移除可能引起问题的参数
            })
        else:  # libx264软件编码
            params.update({
                "preset": "slow",  # 慢速预设，提高质量
                "crf": "18",       # 高质量CRF值
                "profile:v": "high",
                # 移除level参数
                "refs": 5,         # 参考帧数量
                "psy": "1",        # 启用心理视觉优化
                "psy-rd": "1.0:0.05"  # 心理视觉优化率失真
            })
        
        # 为4K视频添加额外参数
        if is_4k:
            # 增加4K视频的颜色属性和质量设置
            extra_params = {
                "pix_fmt": "yuv420p10le" if hw_accel == "libx264" else "yuv420p", # 10-bit色深(仅软件编码)
                "colorspace": "bt709",     # 标准色彩空间
                "color_primaries": "bt709", # 色彩原色
                "color_trc": "bt709",       # 色彩传输特性
                "movflags": "+faststart"    # 文件元数据前置，便于快速播放
            }
            params.update(extra_params)
        
        return params


if __name__ == "__main__":
    # 测试旋转检测
    import sys
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        print(f"测试文件: {test_file}")
        
        # 详细输出测试文件的所有信息
        print("\n===== 视频详细信息 =====")
        try:
            info_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", 
                       "-show_entries", "stream=width,height,codec_name,display_aspect_ratio", 
                       "-of", "json", test_file]
            info_result = subprocess.run(info_cmd, capture_output=True, text=True)
            
            if info_result.returncode == 0:
                video_info = json.loads(info_result.stdout)
                if "streams" in video_info and video_info["streams"]:
                    stream = video_info["streams"][0]
                    width = int(stream.get("width", 0))
                    height = int(stream.get("height", 0))
                    codec = stream.get("codec_name", "").lower()
                    aspect_ratio = stream.get("display_aspect_ratio", "")
                    
                    print(f"视频尺寸: {width}x{height}")
                    print(f"编码格式: {codec}")
                    print(f"显示宽高比: {aspect_ratio}")
                    print(f"视频方向: {'竖屏' if height > width else '横屏'}")
                    
                    if height > 0:
                        actual_ratio = width / height
                        print(f"实际宽高比: {actual_ratio:.4f}")
                        
                        if 1.7 < actual_ratio < 1.8:
                            print("宽高比接近16:9")
                        elif 0.5 < actual_ratio < 0.6:
                            print("宽高比接近9:16")
        except Exception as e:
            print(f"获取视频信息失败: {str(e)}")
            
        print("\n===== 旋转检测 =====")
        rotation = test_rotation_detection(test_file)
        print(f"检测到的旋转角度: {rotation}")
        
        print("\n===== 编码检测 =====")
        codec = get_video_codec(test_file)
        print(f"检测到的编码: {codec}")
        
        # 特殊情况判断
        print("\n===== 特殊情况判断 =====")
        is_portrait_orientation = height > width
        is_4k = (width >= 3840 or height >= 3840)
        is_hevc = codec.lower() == 'hevc'
        
        print(f"是否竖屏: {is_portrait_orientation}")
        print(f"是否4K视频: {is_4k}")
        print(f"是否HEVC编码: {is_hevc}")
        print(f"是否4K HEVC视频: {is_4k and is_hevc}")
        
        if is_4k and is_hevc:
            print("这是4K HEVC视频，需要特殊处理")
            
            aspect_ratio = width / height if height > 0 else 0
            if 1.7 < aspect_ratio < 1.8 and rotation == 0:
                print("这是16:9的横屏4K HEVC视频，不需要旋转")
            elif rotation != 0:
                print(f"这是有旋转信息的4K HEVC视频，旋转角度: {rotation}°")
        
        if not is_portrait_orientation and rotation == 0:
            print("这是横屏视频，无旋转信息")
            aspect_ratio = width / height if height > 0 else 0
            if 1.7 < aspect_ratio < 1.8:
                print("这是标准16:9横屏视频")
        
        sys.exit(0)
        
    # 原有的测试代码
    m = MaterialInfo()
    m.url = "/Users/harry/Downloads/IMG_2915.JPG"
    m.provider = "local"
    materials = preprocess_video([m], clip_duration=4)
    print(materials)
