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
            
            subprocess.run(bgm_cmd, check=True, capture_output=True)
            
            if not os.path.exists(processed_bgm) or os.path.getsize(processed_bgm) == 0:
                logger.error("背景音乐处理失败")
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
        subprocess.run(audio_cmd, check=True, capture_output=True)
        
        if not os.path.exists(merged_audio) or os.path.getsize(merged_audio) == 0:
            logger.error("音频处理失败")
            return None
            
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
            subprocess.run(subtitle_cmd, check=True, capture_output=True)
            
            if os.path.exists(ass_subtitle):
                # 安全处理路径中的特殊字符
                safe_subtitle_path = ass_subtitle.replace(":", "\\:")
                subtitle_filter = f"subtitles={safe_subtitle_path}:force_style='FontName={params.font_name},FontSize={params.font_size},PrimaryColour=&H{params.text_fore_color[1:]}&,OutlineColour=&H{params.stroke_color[1:]}&,BorderStyle=1,Outline={params.stroke_width},Alignment={alignment}'"

        # 构建最终滤镜字符串
        filter_complex = []
        
        if subtitle_filter:
            filter_complex.append(subtitle_filter)

        # 最终合并视频、音频和字幕
        final_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", merged_audio
        ]
        
        # 应用滤镜（如果有）
        if filter_complex:
            final_cmd.extend(["-vf", ",".join(filter_complex)])
        
        # 输出参数
        final_cmd.extend([
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "copy",
            "-shortest",
            output_file
        ])
        
        logger.info("生成最终视频...")
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
        
        final_process.wait()
        
        if final_process.returncode != 0 or not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            logger.error("最终视频生成失败")
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
    # 原有的测试代码
    m = MaterialInfo()
    m.url = "/Users/harry/Downloads/IMG_2915.JPG"
    m.provider = "local"
    materials = VideoPreprocessor.preprocess_video([m], clip_duration=4)
    print(materials)
