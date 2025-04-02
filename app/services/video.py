"""
视频处理服务模块

优化记录：
1. 消除重复的元数据提取:
   - 使用VideoMetadataExtractor提取一次视频元数据并缓存
   - 后续处理过程中使用缓存的元数据，避免重复提取

2. 避免重复的旋转处理:
   - 在预处理阶段完成物理旋转并更新元数据
   - 标记已处理的视频(`_processed`, `rotation_handled`)
   - 下游处理检测已处理标记，避免重复旋转

3. 优化重复的缩放和填充:
   - 在预处理阶段完成所需的缩放和填充
   - 处理过的视频会被跳过后续缩放和填充操作
   - 通过元数据缓存和文件名标记识别预处理过的视频
"""

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
import sys

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
    try:
        # 确保输出目录存在
        os.makedirs(os.path.dirname(combined_video_path), exist_ok=True)
        
        # 创建临时目录
        temp_dir = os.path.join(os.path.dirname(combined_video_path), "temp_combine_" + str(uuid.uuid4()))
        os.makedirs(temp_dir, exist_ok=True)
        
        # 记录需要清理的临时文件
        processed_paths = []
        segment_files = []
        
        # 获取音频时长
        audio_probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_file]
        audio_info = json.loads(subprocess.check_output(audio_probe_cmd, universal_newlines=True))
        audio_duration = float(audio_info["format"]["duration"])
        logger.info(f"音频时长: {audio_duration} 秒")
        
        # 设置视频分辨率
        aspect = VideoAspect(video_aspect)
        target_width, target_height = aspect.to_resolution()
        logger.info(f"目标视频分辨率: {target_width}x{target_height}")
        
        # 处理每个视频
        processed_segments = []
        segment_index = 0
        
        for idx, video_path in enumerate(video_paths):
            try:
                if not os.path.exists(video_path):
                    logger.error(f"视频文件不存在: {video_path}")
                    continue
                
                logger.info(f"处理视频 {idx+1}/{len(video_paths)}: {os.path.basename(video_path)}")
                
                # 检查视频是否已经预处理过
                filename = os.path.basename(video_path)
                is_preprocessed = "_processed" in filename or "proc_" in filename
                
                # 使用缓存的元数据提取视频信息
                metadata = VideoMetadataExtractor.get_video_metadata(video_path)
                
                if not metadata or metadata.width == 0 or metadata.height == 0:
                    logger.error(f"无法获取视频元数据: {video_path}")
                    continue
                
                # 提取关键信息
                width = metadata.width
                height = metadata.height
                rotation = metadata.rotation
                codec = metadata.codec
                v_duration = metadata.duration
                
                # 计算有效的宽高（考虑旋转）
                effective_width = metadata.effective_width if metadata.effective_width > 0 else width
                effective_height = metadata.effective_height if metadata.effective_height > 0 else height
                
                # 如果元数据中的有效宽高为0，手动计算
                if effective_width == 0 or effective_height == 0:
                    effective_width, effective_height = width, height
                    if rotation in [90, 270, -90]:
                        effective_width, effective_height = height, width
                
                logger.info(f"视频信息: 宽={width}, 高={height}, 编码={codec}, 旋转={rotation}°")
                
                # 获取视频时长（如果元数据中无时长，尝试获取）
                if v_duration <= 0:
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
                    
                    # 构造截取片段命令
                    segment_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(start_time),
                        "-i", video_path,
                        "-t", str(segment_duration)
                    ]
                    
                    # 只有未预处理的视频才需要添加滤镜
                    if not is_preprocessed:
                        # 应用视频滤镜（如果需要）
                        filters = []
                        
                        # 添加像素格式确保兼容性
                        filters.append("format=yuv420p")
                        
                        # 组合所有滤镜
                        if filters:
                            vf_filter = ",".join(filters)
                            segment_cmd.extend(["-vf", vf_filter])
                    
                    # 添加输出参数
                    segment_cmd.extend([
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-pix_fmt", "yuv420p",
                        segment_path
                    ])
                    
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
                                "-vf", "format=yuv420p",
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
        
        # 使用concat分离器合并视频片段
        concat_output_path = os.path.join(temp_dir, "concat_output.mp4")
        
        # 首先统一所有片段的格式和帧率
        normalized_segments = []
        for i, segment in enumerate(segment_files):
            normalized_segment = os.path.join(temp_dir, f"normalized_{i:03d}.mp4")
            normalize_cmd = [
                "ffmpeg", "-y",
                "-i", segment,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-r", "60",  # 强制60fps
                "-vsync", "cfr",  # 使用固定帧率
                normalized_segment
            ]
            try:
                subprocess.run(normalize_cmd, check=True, capture_output=True)
                normalized_segments.append(normalized_segment)
            except subprocess.CalledProcessError as e:
                logger.error(f"标准化视频片段失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
                normalized_segments.append(segment)  # 如果失败，使用原始片段
        
        # 创建新的片段列表文件
        segments_list_path = os.path.join(temp_dir, "segments.txt")
        with open(segments_list_path, "w") as f:
            for segment in normalized_segments:
                f.write(f"file '{segment}'\n")
        
        # 使用concat分离器合并视频片段
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", segments_list_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", "60",
            "-vsync", "cfr",
            "-max_muxing_queue_size", "1024",
            concat_output_path
        ]
        
        try:
            logger.info("合并视频片段...")
            subprocess.run(concat_cmd, check=True, capture_output=True)
            
            # 验证合并后的视频时长
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                concat_output_path
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            if probe_result.returncode == 0:
                duration_info = json.loads(probe_result.stdout)
                total_duration = float(duration_info["format"]["duration"])
                logger.info(f"合并后视频总时长: {total_duration:.2f}秒")
                
                # 如果时长明显不对，尝试使用filter_complex方式重新合并
                if total_duration > sum(segment_duration for segment_duration in [10.0, 4.16, 10.0]):
                    logger.warning("检测到时长异常，使用filter_complex方式重新合并")
                    
                    # 创建concat文件，使用demuxer方式重新合并
                    concat_file = os.path.join(temp_dir, "concat.txt")
                    with open(concat_file, "w", encoding="utf-8") as f:
                        for segment in normalized_segments:
                            f.write(f"file '{segment}'\n")
                    
                    # 使用concat demuxer方式合并视频
                    new_concat_cmd = [
                        "ffmpeg", "-y",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", concat_file,
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-pix_fmt", "yuv420p",
                        "-r", "60",
                        "-vsync", "cfr",
                        concat_output_path
                    ]
                    
                    logger.info(f"使用concat demuxer方式重新合并: {' '.join(new_concat_cmd)}")
                    
                    try:
                        subprocess.run(new_concat_cmd, check=True, capture_output=True)
                        
                        # 再次验证时长
                        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
                        if probe_result.returncode == 0:
                            duration_info = json.loads(probe_result.stdout)
                            new_duration = float(duration_info["format"]["duration"])
                            logger.info(f"重新合并后视频总时长: {new_duration:.2f}秒")
                    except subprocess.CalledProcessError as e:
                        logger.error(f"使用concat demuxer方式合并失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
                        
                        # 如果demuxer方式也失败，尝试使用改进的filter_complex方式
                        logger.warning("尝试使用改进的filter_complex方式合并")
                        
                        # 构建改进的filter_complex字符串
                        filter_parts = []
                        
                        # 第1步：为每个输入添加setpts过滤器
                        for i in range(len(normalized_segments)):
                            filter_parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")
                        
                        # 第2步：构建单个concat过滤器，连接所有视频
                        concat_inputs = "".join(f"[v{i}]" for i in range(len(normalized_segments)))
                        filter_parts.append(f"{concat_inputs}concat=n={len(normalized_segments)}:v=1:a=0[outv]")
                        
                        # 构建新的合并命令
                        complex_cmd = ["ffmpeg", "-y"]
                        for segment in normalized_segments:
                            complex_cmd.extend(["-i", segment])
                        
                        complex_cmd.extend([
                            "-filter_complex", ";".join(filter_parts),
                            "-map", "[outv]",
                            "-c:v", "libx264",
                            "-preset", "fast",
                            "-crf", "23",
                            "-pix_fmt", "yuv420p",
                            "-r", "60",
                            concat_output_path
                        ])
                        
                        logger.info(f"使用改进的filter_complex方式合并: {' '.join(complex_cmd)}")
                        
                        try:
                            subprocess.run(complex_cmd, check=True, capture_output=True)
                            
                            # 再次验证时长
                            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
                            if probe_result.returncode == 0:
                                duration_info = json.loads(probe_result.stdout)
                                new_duration = float(duration_info["format"]["duration"])
                                logger.info(f"改进方式合并后视频总时长: {new_duration:.2f}秒")
                        except subprocess.CalledProcessError as e:
                            logger.error(f"使用改进的filter_complex方式合并也失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
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
            "-shortest",  # 确保输出长度最短
            "-vsync", "cfr",  # 使用固定帧率
            "-r", "60",  # 设置固定帧率为60fps
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
        pass
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
    生成最终视频，包括音频和字幕
    
    Args:
        video_path: 视频文件路径
        audio_path: 音频文件路径
        subtitle_path: 字幕文件路径
        output_file: 输出文件路径
        params: 视频参数
        
    Returns:
        生成的视频文件路径
    """
    try:
        logger.info(f"生成视频: {os.path.basename(output_file)}")
        
        # 检查输入文件
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return None
        
        if not os.path.exists(audio_path):
            logger.error(f"音频文件不存在: {audio_path}")
            return None
            
        # 创建临时目录
        temp_dir = os.path.join(os.path.dirname(output_file), f"temp_gen_{str(uuid.uuid4())}")
        os.makedirs(temp_dir, exist_ok=True)
        
        # 检查视频是否已预处理过
        filename = os.path.basename(video_path)
        is_preprocessed = "_processed" in filename or "proc_" in filename
        
        # 获取视频元数据
        metadata = VideoMetadataExtractor.get_video_metadata(video_path)
        if not metadata or metadata.width == 0 or metadata.height == 0:
            logger.error(f"无法获取视频元数据: {video_path}")
            return None
            
        # 设置目标尺寸
        aspect = VideoAspect(params.video_aspect)
        target_width, target_height = aspect.to_resolution()
        logger.info(f"目标视频尺寸: {target_width}x{target_height}")
        
        # 获取音频时长确定最终视频长度
        audio_info_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_path]
        audio_info = json.loads(subprocess.check_output(audio_info_cmd, universal_newlines=True))
        audio_duration = float(audio_info["format"]["duration"])
        logger.info(f"音频时长: {audio_duration:.2f}秒")
        
        # 处理背景音乐
        bgm_file = get_bgm_file(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
        processed_bgm = None
        
        if bgm_file and os.path.exists(bgm_file):
            logger.info(f"处理背景音乐: {os.path.basename(bgm_file)}")
            processed_bgm = os.path.join(temp_dir, "processed_bgm.mp3")
            bgm_cmd = [
                "ffmpeg", "-y",
                "-i", bgm_file,
                "-af", f"volume={params.bgm_volume},afade=t=out:st={audio_duration-3}:d=3",
                "-to", str(audio_duration),
                processed_bgm
            ]
            
            try:
                subprocess.run(bgm_cmd, check=True, capture_output=True)
                if not os.path.exists(processed_bgm) or os.path.getsize(processed_bgm) == 0:
                    logger.error("背景音乐处理失败")
                    processed_bgm = None
            except subprocess.CalledProcessError as e:
                logger.error(f"背景音乐处理失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
                processed_bgm = None
        
        # 合并音频（主音频和背景音乐）
        merged_audio = os.path.join(temp_dir, "merged_audio.aac")
        
        if processed_bgm:
            # 使用filter_complex混合音频
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
        try:
            subprocess.run(audio_cmd, check=True, capture_output=True)
            if not os.path.exists(merged_audio) or os.path.getsize(merged_audio) == 0:
                logger.error("音频处理失败")
                return None
        except subprocess.CalledProcessError as e:
            logger.error(f"音频处理失败: {e.stderr.decode('utf-8', errors='replace') if e.stderr else ''}")
            return None
            
        # 处理字幕
        subtitle_filter = ""
        if subtitle_path and os.path.exists(subtitle_path) and params.subtitle_enabled:
            try:
                # 准备字体
                font_path = ""
                if not params.font_name:
                    params.font_name = "STHeitiMedium.ttc"
                font_path = os.path.join(utils.font_dir(), params.font_name)
                
                logger.info(f"处理字幕文件: {os.path.basename(subtitle_path)}")
                
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
                        
                        # 确定字幕位置
                        alignment = 2  # 默认底部居中
                        if params.subtitle_position == "top":
                            alignment = 8  # 顶部居中
                        elif params.subtitle_position == "center":
                            alignment = 5  # 中间居中
                            
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
                            video_path
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
            except Exception as e:
                logger.error(f"处理字幕时出错: {str(e)}")
                subtitle_filter = ""

        # 构建基本滤镜
        filter_complex = []
        
        # 添加可能需要的其他滤镜
        if is_preprocessed:
            # 如果已预处理，只添加必要的滤镜
            filter_complex.append("format=yuv420p")

        # 添加水印滤镜
        # 获取真实视频尺寸
        video_width = metadata.width
        video_height = metadata.height
        
        # 创建水印滤镜 - 使用项目自带的字体目录
        watermark_vf = ""
        try:
            # 尝试获取与字幕相同的字体
            font_name = params.font_name if params.font_name else "STHeitiMedium.ttc"
            font_path = os.path.join(utils.font_dir(), font_name)
            
            # 如果字体不存在，尝试项目中的其他常用字体
            if not os.path.exists(font_path):
                for common_font in ["STHeitiMedium.ttc", "arial.ttf", "simhei.ttf"]:
                    test_path = os.path.join(utils.font_dir(), common_font)
                    if os.path.exists(test_path):
                        font_path = test_path
                        logger.info(f"使用替代字体: {common_font}")
                        break
            
            # 确保字体存在
            if os.path.exists(font_path):
                # 准备字体路径
                safe_font_path = os.path.abspath(font_path).replace('\\', '/')
                
                # Windows特殊处理
                if os.name == "nt" and ":" in safe_font_path:
                    drive, path = safe_font_path.split(":", 1)
                    safe_font_path = f"{drive}\\:{path}"
                
                # 添加引号                                             
                if not safe_font_path.startswith("'") and not safe_font_path.endswith("'"):
                    safe_font_path = f"'{safe_font_path}'"
                
                watermark_font_size = params.watermark_size
                # 创建带有字体的水印滤镜 - 使用计算表达式替代center关键字
                watermark_vf = f"drawtext=text='{params.watermark_text}':fontfile={safe_font_path}:fontsize={watermark_font_size}:fontcolor=white@0.3:x=(w-text_w)/2:y=(h-text_h)/2"
                logger.info(f"创建带字体的水印滤镜: {watermark_vf}")
            else:
                # 找不到字体时使用简单版本
                watermark_vf = f"drawtext=text='{params.watermark_text}':fontsize={watermark_font_size}:fontcolor=white@0.3:x=(w-text_w)/2:y=(h-text_h)/2"
                logger.info(f"未找到字体，使用简单水印滤镜: {watermark_vf}")
        except Exception as e:
            # 出错时使用最简单版本
            watermark_vf = f"drawtext=text='{params.watermark_text}':fontsize={watermark_font_size}:fontcolor=white@0.3:x=(w-text_w)/2:y=(h-text_h)/2"
            logger.error(f"创建水印滤镜出错: {str(e)}，使用简单版本")
        
        logger.info(f"最终水印滤镜: {watermark_vf}")
        
        # 最终视频处理命令
        if subtitle_filter:
            # 有字幕的情况下，使用复杂filter_complex
            vf = ""
            if filter_complex:
                # 如果有其他滤镜，先添加
                base_filters = ",".join(filter_complex)
                vf = f"{base_filters},{subtitle_filter}"
            else:
                vf = subtitle_filter
            
            # 为Windows特殊处理，避免命令行过长
            if os.name == 'nt':
                # 创建滤镜文件 - 仅包含字幕滤镜
                filters_file = os.path.join(temp_dir, "subtitle_filters.txt")
                try:
                    with open(filters_file, "w", encoding="utf-8") as f:
                        f.write(vf)  # 只写入字幕滤镜
                    
                    # 先应用字幕滤镜，生成中间视频
                    intermediate_video = os.path.join(temp_dir, "with_subtitle.mp4")
                    subtitle_cmd = [
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-filter_complex_script", filters_file,
                        "-c:v", "libx264",
                        "-preset", "ultrafast",  # 使用超快速预设，因为这只是中间文件
                        "-crf", "18",  # 保持较高质量
                        "-pix_fmt", "yuv420p",
                        intermediate_video
                    ]
                    
                    logger.info("第一步：应用字幕滤镜...")
                    logger.info(f"字幕滤镜: {vf}")
                    subprocess.run(subtitle_cmd, check=True, capture_output=True)
                    
                    # 检查中间文件是否生成成功
                    if not os.path.exists(intermediate_video) or os.path.getsize(intermediate_video) == 0:
                        raise Exception("应用字幕滤镜后，中间文件创建失败")
                    
                    # 第二步：应用水印滤镜到中间视频，并添加音频
                    logger.info("第二步：应用水印滤镜并添加音频...")
                    logger.info(f"水印滤镜: {watermark_vf}")
                    
                    final_cmd = [
                        "ffmpeg", "-y",
                        "-i", intermediate_video,
                        "-i", merged_audio,
                        "-vf", watermark_vf,  # 应用水印滤镜
                        "-map", "0:v",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-c:a", "copy",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        "-max_muxing_queue_size", "1024",
                        "-movflags", "+faststart",
                        output_file
                    ]
                except Exception as e:
                    logger.error(f"应用滤镜失败: {str(e)}")
                    # 回退到没有滤镜的版本
                    final_cmd = [
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-i", merged_audio,
                        "-map", "0:v",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-c:a", "copy",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        "-max_muxing_queue_size", "1024",
                        "-movflags", "+faststart",
                        output_file
                    ]
            else:
                # 非Windows系统，也采用两步处理方式确保最大兼容性
                try:
                    # 先应用字幕滤镜，生成中间视频
                    intermediate_video = os.path.join(temp_dir, "with_subtitle.mp4")
                    subtitle_cmd = [
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-filter_complex", vf,  # 应用字幕滤镜
                        "-c:v", "libx264",
                        "-preset", "ultrafast",  # 使用超快速预设
                        "-crf", "18",  # 保持较高质量
                        "-pix_fmt", "yuv420p",
                        intermediate_video
                    ]
                    
                    logger.info("第一步：应用字幕滤镜...")
                    logger.info(f"字幕滤镜: {vf}")
                    subprocess.run(subtitle_cmd, check=True, capture_output=True)
                    
                    # 检查中间文件是否生成成功
                    if not os.path.exists(intermediate_video) or os.path.getsize(intermediate_video) == 0:
                        raise Exception("应用字幕滤镜后，中间文件创建失败")
                    
                    # 第二步：应用水印滤镜到中间视频，并添加音频
                    logger.info("第二步：应用水印滤镜并添加音频...")
                    logger.info(f"水印滤镜: {watermark_vf}")
                    
                    final_cmd = [
                        "ffmpeg", "-y",
                        "-i", intermediate_video,
                        "-i", merged_audio,
                        "-vf", watermark_vf,  # 应用水印滤镜
                        "-map", "0:v",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-c:a", "copy",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        "-max_muxing_queue_size", "1024",
                        "-movflags", "+faststart",
                        output_file
                    ]
                except Exception as e:
                    logger.error(f"应用滤镜失败: {str(e)}")
                    # 在某一步失败时回退到基本方式
                    final_cmd = [
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-i", merged_audio,
                        "-filter_complex", vf,
                        "-map", "0:v",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-c:a", "copy",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        "-max_muxing_queue_size", "1024",
                        "-movflags", "+faststart",
                        output_file
                    ]
        else:
            # 只有水印，没有字幕
            vf = watermark_vf
            if filter_complex:
                vf = f"{','.join(filter_complex)},{watermark_vf}"
            
            # 在Windows和非Windows系统上使用相同的简单方法
            final_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", merged_audio,
                "-vf", vf,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",
                "-shortest",
                "-pix_fmt", "yuv420p",
                "-max_muxing_queue_size", "1024",
                "-movflags", "+faststart",
                output_file
            ]
        
        logger.info("生成最终视频...")
        logger.info(f"FFmpeg命令: {' '.join(final_cmd)}")
        
        final_process = subprocess.Popen(
            final_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            universal_newlines=True
        )
        
        # 显示处理进度
        for line in final_process.stderr:
            if "time=" in line and "bitrate=" in line:
                logger.info(f"视频合成进度: {line.strip()}")
            elif "error" in line.lower():
                logger.error(f"FFmpeg错误: {line.strip()}")
        
        final_process.wait()
        
        if final_process.returncode != 0:
            error_output = final_process.stderr.read()
            logger.error(f"视频生成失败，FFmpeg返回码: {final_process.returncode}")
            logger.error(f"错误输出: {error_output}")
            return None
            
        if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            logger.error("最终视频文件不存在或为空")
            return None
            
        logger.success(f"视频生成成功: {os.path.basename(output_file)}")
        return output_file
        
    except Exception as e:
        logger.error(f"视频生成过程中出错: {str(e)}")
        return None
    finally:
        # 清理临时文件
        try:
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                logger.info("已清理临时文件")
        except Exception as e:
            logger.error(f"清理临时文件失败: {str(e)}")


if __name__ == "__main__":
    # 测试代码
    import sys
    
    def test_watermark():
        """测试水印功能"""
        if len(sys.argv) < 3:
            print("使用方法: python -m app.services.video test-watermark 视频文件路径")
            return
            
        input_video = sys.argv[2]
        if not os.path.exists(input_video):
            print(f"错误: 视频文件不存在: {input_video}")
            return
            
        # 获取视频元数据
        try:
            # 使用JSON格式获取视频尺寸
            json_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                input_video
            ]
            json_result = subprocess.run(json_cmd, capture_output=True, encoding='utf-8', errors='replace', check=False).stdout
            video_info = json.loads(json_result)
            
            width = 1920
            height = 1080
            if "streams" in video_info and video_info["streams"]:
                width = int(video_info["streams"][0].get("width", 1920))
                height = int(video_info["streams"][0].get("height", 1080))
                print(f"视频尺寸: {width}x{height}")
            else:
                print("警告: 未找到视频流信息，使用默认尺寸: 1920x1080")
        except Exception as e:
            print(f"警告: 获取视频尺寸失败: {str(e)}，使用默认尺寸: 1920x1080")
            width = 1920
            height = 1080
            
        # 输出文件名
        output_video = os.path.splitext(input_video)[0] + "_watermarked" + os.path.splitext(input_video)[1]
        
        # 使用简单的水印方式 - 不需要字体文件
        font_size = 36  # 测试用字体大小
        watermark_vf = f"drawtext=text='PC':fontsize={font_size}:fontcolor=white@0.3:x=10:y=10"
        print(f"使用简单水印滤镜: {watermark_vf}")
        
        # 构建命令
        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", input_video,
                "-vf", watermark_vf,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",
                "-pix_fmt", "yuv420p",
                output_video
            ]
            
            print("执行命令:", " ".join(cmd))
            result = subprocess.run(cmd, check=True, capture_output=True, encoding='utf-8', errors='replace')
            print(f"水印添加成功！输出文件: {output_video}")
            
            # 显示命令输出
            print("\n命令输出:")
            print(result.stdout)
            
            if result.stderr:
                print("\n错误信息:")
                print(result.stderr)
        except subprocess.CalledProcessError as e:
            print(f"错误: 添加水印失败: {e}")
            print("\n命令输出:")
            print(e.stdout)
            print("\n错误信息:")
            print(e.stderr)
            
            # 尝试备用方法 - 使用更简单的配置
            try:
                print("\n尝试使用备用方法...")
                backup_cmd = [
                    "ffmpeg", "-y",
                    "-i", input_video,
                    "-vf", "drawtext=text='PC':fontsize=36:fontcolor=white@0.5:x=10:y=10",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",  # 使用超快速预设
                    "-crf", "28",  # 降低质量要求
                    "-c:a", "copy",
                    "-pix_fmt", "yuv420p",
                    output_video
                ]
                
                print("执行备用命令:", " ".join(backup_cmd))
                result = subprocess.run(backup_cmd, check=True)
                print(f"使用备用方法添加水印成功！输出文件: {output_video}")
            except Exception as e2:
                print(f"备用方法也失败: {str(e2)}")
        except Exception as e:
            print(f"执行命令时出错: {str(e)}")
    
    # 根据命令行参数执行不同的测试
    if len(sys.argv) > 1:
        if sys.argv[1] == "test-watermark":
            test_watermark()
        else:
            print(f"未知的测试命令: {sys.argv[1]}")
    else:
        print("请指定测试命令: test-watermark")
