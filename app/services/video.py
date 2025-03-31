import glob
import os
import random
from typing import List, Optional
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

from app.services.video_metadata import VideoMetadataExtractor
from app.services.preprocess_video import VideoPreprocessor
from app.services.video_encoder import EncoderConfig
from app.services.video_processing import VideoProcessor

# 预处理视频 给外部调用
def preprocess_video(materials: List[MaterialInfo], clip_duration=4, video_aspect: VideoAspect = VideoAspect.portrait):
    """
    使用ffmpeg处理视频和图片素材
    """
    # 调用新模块的实现
    return VideoPreprocessor.preprocess_video(materials, clip_duration, video_aspect)


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
        target_width, target_height = aspect.to_resolution()
        logger.info(f"目标视频分辨率: {target_width}x{target_height}")
        
        # 预处理并裁剪每个视频
        processed_segments = []
        segment_index = 0
        
        processed_video_paths = []

        for idx, video_path in enumerate(video_paths):
            try:
                if not os.path.exists(video_path):
                    logger.error(f"视频文件不存在: {video_path}")
                    continue

                    processed_video_paths.append(video_path)
            except Exception as e:
                logger.error(f"预处理视频失败: {str(e)}")
                processed_video_paths.append(video_path)  # 添加原始文件作为后备

        # 使用处理后的视频路径列表
        video_paths = processed_video_paths
        
        for idx, video_path in enumerate(video_paths):
            try:
                logger.info(f"处理视频 {idx+1}/{len(video_paths)}: {os.path.basename(video_path)}")
                
                # 使用VideoMetadataExtractor获取视频元数据
                metadata = VideoMetadataExtractor.get_video_metadata(video_path)
                
                if not metadata or metadata.width == 0 or metadata.height == 0:
                    logger.error(f"无法获取视频元数据: {video_path}")
                    continue
                
                # 提取关键信息
                width = metadata.width
                height = metadata.height
                rotation = metadata.rotation
                codec = metadata.codec
                
                logger.info(f"视频信息: 宽={width}, 高={height}, 编码={codec}, 旋转={rotation}°")
                
                # 确定视频帧率
                fps = 30  # 默认帧率
                
                try:
                    # 获取帧率信息
                    fps_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
                    fps_output = subprocess.check_output(fps_cmd, universal_newlines=True).strip()
                    
                    if fps_output:
                        fps_parts = fps_output.split('/')
                        if len(fps_parts) == 2 and int(fps_parts[1]) != 0:
                            fps = float(fps_parts[0]) / float(fps_parts[1])
                        elif len(fps_parts) == 1:
                            fps = float(fps_parts[0])
                except Exception as e:
                    logger.warning(f"获取视频帧率失败，使用默认值30fps: {str(e)}")
                
                logger.info(f"视频帧率: {fps}fps")
                
                # 计算旋转后的有效尺寸
                effective_width, effective_height = width, height
                if rotation in [90, 270, -90]:
                    effective_width, effective_height = height, width
                
                # 获取视频时长
                v_duration = 0
                if hasattr(metadata, 'duration') and metadata.duration > 0:
                    v_duration = metadata.duration
                else:
                    # 如果元数据中没有时长，尝试获取
                    try:
                        duration_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path]
                        duration_output = subprocess.check_output(duration_cmd, universal_newlines=True).strip()
                        if duration_output:
                            v_duration = float(duration_output)
                    except Exception as e:
                        logger.warning(f"获取视频时长失败: {str(e)}")
                        v_duration = 10.0  # 设置一个默认值
                
                # 判断视频方向
                is_portrait = effective_height > effective_width
                
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
                    
                    # 应用旋转和缩放滤镜
                    filters = []
          
                    # 3. 添加像素格式确保兼容性
                    filters.append("format=yuv420p")
                    
                    # 组合所有滤镜
                    vf_filter = ",".join(filters)
                    
                    # 构造截取片段命令
                    segment_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(start_time),
                        "-i", video_path,
                        "-t", str(segment_duration),
                        "-vf", vf_filter,
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-pix_fmt", "yuv420p"
                    ]
                    
            
                    
                    segment_cmd.append(segment_path)
                    
                    logger.info(f"片段处理命令: {' '.join(segment_cmd)}")
                    
                    # 执行命令
                    try:
                        subprocess.run(segment_cmd, check=True, capture_output=True)
                    except subprocess.CalledProcessError as e:
                        error_msg = e.stderr.decode('utf-8', errors='replace') if e.stderr else ''
                        logger.error(f"处理视频片段失败: {error_msg}")
                        
                        # 尝试使用备用简化命令
                        if "Invalid too big or non positive size" in error_msg or "Error initializing filter" in error_msg:
                            logger.warning("使用备用简化命令处理视频片段")
                            backup_cmd = [
                                "ffmpeg", "-y",
                                "-ss", str(start_time),
                                "-i", video_path,
                                "-t", str(segment_duration),
                                "-vf", "scale=w='min(iw,1920)':h='min(ih,1920)':force_original_aspect_ratio=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
                                "-c:v", "libx264",
                                "-preset", "ultrafast", # 使用更快的预设
                                "-crf", "28", # 降低质量要求确保成功
                                segment_path
                            ]
                            
                            try:
                                subprocess.run(backup_cmd, check=True, capture_output=True)
                            except subprocess.CalledProcessError as e2:
                                logger.error(f"备用命令也失败: {e2.stderr.decode('utf-8', errors='replace') if e2.stderr else ''}")
                                continue
                    
                    if os.path.exists(segment_path) and os.path.getsize(segment_path) > 0:
                        segment_files.append(segment_path)
                        logger.info(f"创建视频片段: {segment_path}, 时长: {segment_duration:.2f}秒")
                    else:
                        logger.error(f"创建视频片段失败: {segment_path}")
            except Exception as e:
                logger.error(f"处理视频失败: {str(e)}")
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
    # finally:
        # 清理临时文件
        # try:
        #     shutil.rmtree(temp_dir)
        # except Exception as e:
        #     logger.warning(f"清理临时目录失败: {str(e)}")


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
    try:
        logger.info("使用ffmpeg生成视频")
        temp_dir = os.path.dirname(output_file)
        return None
        # 创建中间处理文件路径
        processed_video = os.path.join(temp_dir, f"processed_{str(uuid.uuid4())}.mp4")
        
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
        
        # 使用新的VideoProcessor获取视频特性
        video_features = VideoProcessor.get_video_features(video_path)
        if not video_features:
            logger.error("无法获取视频特性，处理终止")
            return None
        
        logger.info(f"视频特性: 宽={video_features['width']}, 高={video_features['height']}, " +
                   f"编码={video_features['codec']}, 旋转={video_features['rotation']}°")
        logger.info(f"视频分类: 4K={video_features['is_4k']}, 高清={video_features['is_hd']}, " +
                   f"需要旋转={video_features['needs_rotation']}")
        
        # 确定目标分辨率
        if params.video_aspect == VideoAspect.portrait:
            target_width = 1080
            target_height = 1920
        else:
            target_width = 1920
            target_height = 1080
        
        # 获取基础编码参数
        encoder_params = EncoderConfig.get_encoder_params(hw_accel, target_width, target_height)
        
        # 基于视频特性优化编码参数
        optimized_params = VideoProcessor.optimize_encoding_params(video_features, encoder_params)
        logger.info(f"优化后的编码参数: {optimized_params}")
        
        # 提取编码参数
        bitrate = optimized_params["bitrate"]
        maxrate = optimized_params["maxrate"]
        bufsize = optimized_params["bufsize"]
        
        # 获取其他编码器参数
        encoder_args = []
        for key, value in optimized_params.items():
            if key not in ["bitrate", "maxrate", "bufsize"]:
                encoder_args.extend([f"-{key}", str(value)])
        
        # 确定是否需要处理视频
        needs_resize = video_features["effective_width"] > target_width or video_features["effective_height"] > target_height
        needs_processing = needs_resize or video_features["codec"] != "h264" or video_features["needs_rotation"]
        
        # 构建统一的滤镜字符串
        scale_filter = VideoProcessor.build_filter_string(video_features, target_width, target_height)
        
        # 4K视频特殊处理：保持高质量 (不再限制为HEVC编码)
        if video_features["is_4k"] or (video_features["is_hd"] and video_features["needs_rotation"]):
            # 增加码率以保持高质量视频质量
            bitrate = min(20000, int(bitrate * 1.5))  # 提高码率，最高20Mbps
            maxrate = min(30000, int(maxrate * 1.5))  # 提高最大码率，最高30Mbps
            bufsize = min(40000, int(bufsize * 1.5))  # 提高缓冲大小
            logger.info(f"检测到高清视频，提高码率: {bitrate}k, 最大码率: {maxrate}k")
        
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
                "-metadata:s:v", "rotate=0",  # 明确移除旋转元数据，避免播放器自动旋转
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


if __name__ == "__main__":
    # 原有的测试代码
    m = MaterialInfo()
    m.url = "/Users/harry/Downloads/IMG_2915.JPG"
    m.provider = "local"
    materials = VideoPreprocessor.preprocess_video([m], clip_duration=4)
    print(materials)
