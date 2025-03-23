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
        
        # 获取完整的视频信息
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
        result = subprocess.run(cmd, capture_output=True, text=False)
        
        if result.returncode != 0:
            error_message = result.stderr.decode('utf-8', errors='replace')
            logger.error(f"❌ ffprobe执行失败: {error_message}")
            return 0
        
        # 解码输出
        stdout_bytes = result.stdout
        try:
            stdout_text = stdout_bytes.decode('utf-8', errors='replace')
        except Exception as decode_error:
            logger.error(f"❌ 解码ffprobe输出失败: {str(decode_error)}")
            return 0
        
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
        
        # 3. 如果前两种方法都没找到，尝试直接搜索文本中的旋转信息
        if "rotation of -90" in stdout_text:
            logger.info("🔄 从文本中找到 'rotation of -90'")
            return 90
        elif "rotation of 90" in stdout_text:
            logger.info("🔄 从文本中找到 'rotation of 90'")
            return 270
        elif "rotation of 180" in stdout_text or "rotation of -180" in stdout_text:
            logger.info("🔄 从文本中找到 'rotation of 180'")
            return 180
        
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
        
        result = subprocess.run(cmd, capture_output=True, text=False)
        
        if result.returncode != 0:
            error_message = result.stderr.decode('utf-8', errors='replace')
            logger.error(f"❌ 获取编码信息失败: {error_message}")
            return "unknown"
        
        try:
            data = json.loads(result.stdout.decode('utf-8', errors='replace'))
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

def combine_videos_with_ffmpeg(combined_video_path: str, video_paths: List[str], audio_file: str, 
                              video_aspect: VideoAspect = VideoAspect.portrait,
                              max_clip_duration: int = 5, threads: int = 2):
    """使用纯ffmpeg实现视频混剪，完全绕过MoviePy"""
    try:
        # 首先验证所有输入文件
        for video_path in video_paths:
            if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
                logger.error(f"视频文件无效: {video_path}")
                video_paths.remove(video_path)
        
        if not video_paths:
            logger.error("没有有效的视频文件")
            return None
            
        if not os.path.exists(audio_file):
            logger.error(f"音频文件不存在: {audio_file}")
            return None
        
        # 1. 创建临时目录
        temp_dir = os.path.join(os.path.dirname(combined_video_path), "temp_combine")
        os.makedirs(temp_dir, exist_ok=True)
        
        # 2. 获取音频时长
        audio_cmd = [
            "ffprobe", 
            "-v", "error", 
            "-show_entries", "format=duration", 
            "-of", "csv=p=0", 
            audio_file
        ]
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True)
        
        try:
            audio_duration = float(audio_result.stdout.strip())
            logger.info(f"音频时长: {audio_duration:.2f}秒")
        except:
            logger.error("无法获取音频时长，使用默认值")
            audio_duration = 30.0
        
        # 3. 设置输出视频尺寸
        if video_aspect == VideoAspect.portrait:
            target_width = 1080
            target_height = 1920
        else:
            target_width = 1920
            target_height = 1080
            
        logger.info(f"视频尺寸: {target_width}x{target_height}")
        
        # 4. 处理每个视频片段
        processed_videos = []
        segment_files = []
        
        total_video_count = len(video_paths)
        remaining_duration = audio_duration
        
        for idx, video_path in enumerate(video_paths):
            logger.info(f"处理视频 {idx+1}/{total_video_count}: {os.path.basename(video_path)}")
            
            # 获取视频信息
            info_cmd = [
                "ffprobe", 
                "-v", "error", 
                "-select_streams", "v:0", 
                "-show_entries", "stream=width,height,r_frame_rate,duration", 
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
                
                # 获取视频时长（如果没有，我们会计算）
                try:
                    v_duration = float(stream.get("duration", 0))
                except:
                    v_duration = 0
                    
                if v_duration <= 0:
                    # 如果流中没有时长，尝试从格式信息获取
                    format_cmd = [
                        "ffprobe", 
                        "-v", "error", 
                        "-show_entries", "format=duration", 
                        "-of", "csv=p=0", 
                        video_path
                    ]
                    format_result = subprocess.run(format_cmd, capture_output=True, text=True)
                    try:
                        v_duration = float(format_result.stdout.strip())
                    except:
                        # 如果还是无法获取，则计算帧数/帧率
                        frames_cmd = [
                            "ffprobe", 
                            "-v", "error", 
                            "-count_frames", 
                            "-select_streams", "v:0", 
                            "-show_entries", "stream=nb_read_frames", 
                            "-of", "csv=p=0", 
                            video_path
                        ]
                        frames_result = subprocess.run(frames_cmd, capture_output=True, text=True)
                        try:
                            frame_count_str = frames_result.stdout.strip()
                            # 解析帧数，需要处理可能存在的逗号
                            frame_count = int(frame_count_str.replace(',', ''))
                            v_duration = frame_count / fps
                        except:
                            logger.warning(f"无法计算视频时长，使用默认值")
                            v_duration = 10.0
                
                logger.info(f"视频信息: {v_width}x{v_height}, {fps:.2f}fps, {v_duration:.2f}秒")
                
                # 检测视频编码器
                codec_cmd = [
                    "ffprobe", 
                    "-v", "error", 
                    "-select_streams", "v:0", 
                    "-show_entries", "stream=codec_name", 
                    "-of", "csv=p=0", 
                    video_path
                ]
                codec_result = subprocess.run(codec_cmd, capture_output=True, text=True)
                codec_name = codec_result.stdout.strip()
                
                # 检测旋转角度
                rotation_cmd = [
                    "ffprobe",
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream_side_data=rotation",
                    "-of", "csv=p=0",
                    video_path
                ]
                rotation_result = subprocess.run(rotation_cmd, capture_output=True, text=True)
                rotation = 0
                
                if rotation_result.stdout.strip():
                    try:
                        rotation = int(float(rotation_result.stdout.strip()))
                    except:
                        # 尝试使用display matrix
                        display_cmd = [
                            "ffprobe",
                            "-v", "error",
                            "-select_streams", "v:0",
                            "-show_entries", "stream_side_data=displaymatrix",
                            "-of", "csv=p=0",
                            video_path
                        ]
                        display_result = subprocess.run(display_cmd, capture_output=True, text=True)
                        display_output = display_result.stdout.strip()
                        
                        if "degrees" in display_output:
                            match = re.search(r"(-?\d+(?:\.\d+)?)\s*degrees", display_output)
                            if match:
                                rotation = int(float(match.group(1)))
                
                logger.info(f"视频编码: {codec_name}, 旋转: {rotation}°")
                
                # 确定每个片段的时长
                clip_duration = min(max_clip_duration, v_duration, remaining_duration)
                if clip_duration <= 0:
                    logger.info("音频已满，跳过剩余视频")
                    break
                    
                remaining_duration -= clip_duration
                
                # 生成输出片段文件名
                segment_filename = f"segment_{idx:03d}.mp4"
                segment_path = os.path.join(temp_dir, segment_filename)
                
                # 构建旋转滤镜（如果需要）
                rotate_filter = ""
                if rotation == 90:
                    rotate_filter = "transpose=2,"  # 逆时针旋转90度
                elif rotation == 270 or rotation == -90:
                    rotate_filter = "transpose=1,"  # 顺时针旋转90度
                elif rotation == 180:
                    rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
                
                # 处理HEVC编码的视频
                if codec_name.lower() == 'hevc':
                    # 先进行转码处理
                    hevc_output = os.path.join(temp_dir, f"hevc_converted_{idx:03d}.mp4")
                    
                    # 使用更强大的处理参数
                    hevc_cmd = [
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-map_metadata", "-1",  # 移除所有元数据
                        "-vf", f"{rotate_filter}scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                        "-c:v", "libx264",
                        "-crf", "23",
                        "-preset", "fast",
                        "-pix_fmt", "yuv420p",
                        "-color_primaries", "bt709",
                        "-color_trc", "bt709",
                        "-colorspace", "bt709",
                        "-movflags", "+faststart",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-t", str(clip_duration),
                        "-max_muxing_queue_size", "9999",
                        hevc_output
                    ]
                    
                    logger.info(f"处理HEVC视频: {os.path.basename(video_path)}")
                    hevc_result = subprocess.run(hevc_cmd, capture_output=True, text=True)
                    
                    if hevc_result.returncode != 0:
                        logger.error(f"HEVC转码失败: {hevc_result.stderr}")
                        continue
                        
                    # 使用转码后的视频
                    segment_cmd = [
                        "ffmpeg", "-y",
                        "-i", hevc_output,
                        "-c", "copy",  # 直接复制，不重新编码
                        "-t", str(clip_duration),
                        segment_path
                    ]
                    
                else:
                    # 普通视频直接处理
                    segment_cmd = [
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-map_metadata", "-1",  # 移除所有元数据
                        "-vf", f"{rotate_filter}scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                        "-c:v", "libx264",
                        "-crf", "23",
                        "-preset", "fast",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-t", str(clip_duration),
                        segment_path
                    ]
                
                logger.info(f"创建视频片段: {segment_filename}, 时长: {clip_duration:.2f}秒")
                segment_result = subprocess.run(segment_cmd, capture_output=True, text=True)
                
                if segment_result.returncode != 0:
                    logger.error(f"创建片段失败: {segment_result.stderr}")
                    continue
                    
                # 验证输出视频
                verify_cmd = ["ffprobe", "-v", "error", segment_path]
                verify_result = subprocess.run(verify_cmd)
                
                if verify_result.returncode == 0:
                    segment_files.append(segment_path)
                    logger.success(f"片段创建成功: {segment_filename}")
                else:
                    logger.error(f"片段验证失败")
                
            except Exception as e:
                logger.error(f"处理视频时出错: {str(e)}")
                continue
                
        # 5. 创建片段列表文件
        if not segment_files:
            logger.error("没有有效的视频片段")
            return None
            
        # 创建文件列表
        list_file = os.path.join(temp_dir, "segments.txt")
        with open(list_file, 'w') as f:
            for segment in segment_files:
                f.write(f"file '{segment}'\n")
                
        # 6. 合并片段
        logger.info(f"合并 {len(segment_files)} 个视频片段")
        merged_video = os.path.join(temp_dir, "merged.mp4")
        
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            merged_video
        ]
        
        concat_result = subprocess.run(concat_cmd, capture_output=True, text=True)
        
        if concat_result.returncode != 0:
            logger.error(f"合并视频失败: {concat_result.stderr}")
            return None
            
        # 7. 添加音频
        logger.info(f"添加音频: {os.path.basename(audio_file)}")
        
        final_cmd = [
            "ffmpeg", "-y",
            "-i", merged_video,
            "-i", audio_file,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            combined_video_path
        ]
        
        final_result = subprocess.run(final_cmd, capture_output=True, text=True)
        
        if final_result.returncode != 0:
            logger.error(f"添加音频失败: {final_result.stderr}")
            return None
            
        # 8. 验证最终视频
        verify_final_cmd = ["ffprobe", "-v", "error", combined_video_path]
        verify_final_result = subprocess.run(verify_final_cmd)
        
        if verify_final_result.returncode == 0:
            logger.success(f"视频创建成功: {os.path.basename(combined_video_path)}")
            
            # 清理临时文件
            try:
                shutil.rmtree(temp_dir)
                logger.info("已清理临时文件")
            except:
                logger.warning("清理临时文件失败")
                
            return combined_video_path
        else:
            logger.error("最终视频验证失败")
            return None
            
    except Exception as e:
        logger.error(f"视频合成过程中出错: {str(e)}", exc_info=True)
        return None


# 为了使用上面重构的函数，我们需要添加这个帮助类的静态方法
class VideoMetadataHandler:
    @staticmethod
    def normalize_rotation(rotation: float) -> int:
        """标准化旋转角度（确保是90的倍数，并且为正值）"""
        rotation = int(round(rotation / 90) * 90) % 360
        if rotation < 0:
            rotation = (360 + rotation) % 360
        return rotation
    
    @staticmethod
    def get_video_metadata(file_path: str) -> dict:
        """获取视频元数据"""
        try:
            logger.info(f"🎬 获取视频元数据 | 路径: {file_path}")
            
            # 使用与原函数相同的方法处理
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-print_format", "json",
                file_path
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
                return {"width": 0, "height": 0, "rotation": 0, "aspect_ratio": 0}
            
            # 获取视频尺寸
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
            
            # 获取旋转信息
            rotation = 0
            tags = video_stream.get("tags", {})
            
            if tags and "rotate" in tags:
                try:
                    rotation = int(float(tags.get("rotate", "0")))
                except ValueError:
                    pass
            
            # 检查side_data_list中的Display Matrix
            for side_data in video_stream.get("side_data_list", []):
                if side_data.get("side_data_type") == "Display Matrix" and "rotation" in side_data:
                    rotation = float(side_data.get("rotation", 0))
            
            # 尝试从stdout_text中直接搜索旋转信息
            if rotation == 0:
                if "rotation of -90" in stdout_text:
                    rotation = 90
                elif "rotation of 90" in stdout_text:
                    rotation = 270
                elif "rotation of 180" in stdout_text or "rotation of -180" in stdout_text:
                    rotation = 180
            
            # 标准化旋转角度
            rotation = VideoMetadataHandler.normalize_rotation(rotation)
            
            # 计算宽高比
            aspect_ratio = width / height if height != 0 else 0
            
            return {
                "width": width,
                "height": height,
                "rotation": rotation,
                "aspect_ratio": aspect_ratio
            }
            
        except Exception as e:
            logger.error(f"❌ 获取视频元数据失败: {str(e)}", exc_info=True)
            return {"width": 0, "height": 0, "rotation": 0, "aspect_ratio": 0}


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
    使用MoviePy生成视频，如果失败则尝试使用纯ffmpeg方法
    
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
        # 导入MoviePy模块
        try:
            import moviepy.editor as mp
            from moviepy.video.tools.subtitles import SubtitlesClip
            from moviepy.video.fx.all import resize
        except ImportError as e:
            return generate_video_ffmpeg(
                video_path=video_path,
                audio_path=audio_path,
                subtitle_path=subtitle_path,
                output_file=output_file,
                params=params
            )
            
        logger.info(f"使用MoviePy生成视频")
        
        # 先修复视频确保兼容MoviePy
        processed_video_path = fix_video_for_moviepy(video_path)
        
        # 检查处理后的视频能否被MoviePy读取
        try:
            video_clip = mp.VideoFileClip(processed_video_path)
        except Exception as e:
            logger.error(f"MoviePy无法读取视频，将使用纯ffmpeg方案: {e}")
            return generate_video_ffmpeg(
                video_path=video_path,
                audio_path=audio_path,
                subtitle_path=subtitle_path,
                output_file=output_file,
                params=params
            )
            
        # MoviePy处理逻辑
        logger.info(f"视频尺寸: {video_clip.size}")
        aspect = VideoAspect(params.video_aspect)
        video_width, video_height = aspect.to_resolution()
        
        # 调整视频尺寸
        logger.info(f"调整视频尺寸至: {video_width}x{video_height}")
        resized_clip = resize(video_clip, width=video_width, height=video_height)
        
        # 处理音频
        logger.info(f"处理音频: {audio_path}")
        audio_clip = mp.AudioFileClip(audio_path)
        audio_clip = audio_clip.volumex(params.voice_volume)
        
        # 处理背景音乐
        bgm_file = get_bgm_file(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
        if bgm_file and os.path.exists(bgm_file):
            logger.info(f"添加背景音乐: {bgm_file}")
            bgm_clip = mp.AudioFileClip(bgm_file)
            bgm_clip = bgm_clip.volumex(params.bgm_volume)
            
            # 确保背景音乐长度与主音频相同
            if bgm_clip.duration > audio_clip.duration:
                bgm_clip = bgm_clip.subclip(0, audio_clip.duration)
            else:
                # 如果背景音乐较短，循环到所需长度
                repeats = math.ceil(audio_clip.duration / bgm_clip.duration)
                bgm_clip = mp.concatenate_audioclips([bgm_clip] * repeats)
                bgm_clip = bgm_clip.subclip(0, audio_clip.duration)
            
            # 混合两个音频
            final_audio = mp.CompositeAudioClip([audio_clip, bgm_clip])
        else:
            final_audio = audio_clip
        
        # 设置视频音频
        video_with_audio = resized_clip.set_audio(final_audio)
        
        # 处理字幕
        if subtitle_path and os.path.exists(subtitle_path) and params.subtitle_enabled:
            logger.info(f"添加字幕: {subtitle_path}")
            # 准备字体
            if not params.font_name:
                params.font_name = "STHeitiMedium.ttc"
            font_path = os.path.join(utils.font_dir(), params.font_name)
            
            # 加载字幕
            generator = lambda txt: mp.TextClip(
                txt, 
                font=font_path, 
                fontsize=params.font_size, 
                color=params.text_fore_color,
                stroke_color=params.stroke_color,
                stroke_width=params.stroke_width,
                method="caption",
                size=(video_width, None),
                align="center"
            )
            
            # 确定字幕位置
            position = ("center", "bottom")
            if params.subtitle_position == "top":
                position = ("center", "top")
            elif params.subtitle_position == "center":
                position = ("center", "center")
            
            try:
                subtitles = SubtitlesClip(subtitle_path, generator)
                video_with_subtitles = mp.CompositeVideoClip([video_with_audio, subtitles.set_position(position)])
                final_clip = video_with_subtitles
            except Exception as e:
                logger.error(f"添加字幕失败: {e}")
                final_clip = video_with_audio
        else:
            final_clip = video_with_audio
        
        # 设置最终视频持续时间与音频相同
        final_clip = final_clip.set_duration(final_audio.duration)
        
        # 导出视频
        logger.info(f"导出视频到: {output_file}")
        final_clip.write_videofile(
            output_file,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=os.path.join(os.path.dirname(output_file), f"temp_{uuid.uuid4()}.m4a"),
            remove_temp=True,
            fps=24,
            threads=params.n_threads,
            preset="medium",
            bitrate="8000k"
        )
        
        # 清理
        video_clip.close()
        audio_clip.close()
        final_clip.close()
        
        if processed_video_path != video_path:
            logger.info(f"清理临时文件: {processed_video_path}")
            try:
                os.remove(processed_video_path)
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")
        
        # 检查输出是否成功
        if os.path.exists(output_file):
            logger.success(f"视频生成成功: {os.path.basename(output_file)}")
            return output_file
        else:
            logger.error("MoviePy视频生成失败")
            return None
            
    except Exception as e:
        logger.error(f"使用MoviePy生成视频出错: {str(e)}")
        # 出错时尝试使用纯ffmpeg生成
        logger.info("尝试使用纯ffmpeg方法...")
        return generate_video_ffmpeg(
            video_path=video_path,
            audio_path=audio_path,
            subtitle_path=subtitle_path,
            output_file=output_file,
            params=params
        )


def fix_video_for_moviepy(video_path: str, force_h264: bool = True) -> str:
    """
    修复视频以确保与MoviePy兼容
    
    Args:
        video_path: 原始视频路径
        force_h264: 是否强制转换为H264编码
    
    Returns:
        处理后的视频路径
    """
    logger.info(f"开始修复视频以兼容MoviePy: {os.path.basename(video_path)}")
    
    # 检查视频编码
    codec = get_video_codec(video_path)
    logger.info(f"视频编码: {codec}")
    
    # 检查是否包含不兼容的元数据
    check_cmd = ["ffprobe", "-v", "error", "-show_entries", "frame_tags=side_data_list", "-select_streams", "v", "-of", "json", video_path]
    result = subprocess.run(check_cmd, capture_output=True, text=True)
    has_side_data = False
    
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            frames = data.get('frames', [])
            for frame in frames:
                if 'tags' in frame and 'side_data_list' in frame['tags']:
                    if 'Ambient Viewing Environment' in frame['tags']['side_data_list']:
                        has_side_data = True
                        break
        except Exception as e:
            logger.error(f"解析ffprobe输出失败: {e}")
    
    # 确定是否需要转码
    needs_processing = False
    
    # 如果是HEVC编码或包含不兼容元数据，需要转码
    if codec == "hevc" or has_side_data or force_h264:
        needs_processing = True
        logger.info(f"视频需要转码处理: 编码={codec}, 包含不兼容元数据={has_side_data}")
    
    if not needs_processing:
        logger.info(f"视频不需要特殊处理")
        return video_path
    
    # 创建临时文件用于处理后的视频
    processed_path = os.path.join(os.path.dirname(video_path), f"temp_fix_{uuid.uuid4()}.mp4")
    
    # 获取旋转角度
    rotation = get_video_rotation(video_path)
    rotate_filter = ""
    if rotation == 90:
        rotate_filter = "transpose=1,"
    elif rotation == 180:
        rotate_filter = "transpose=2,transpose=2,"
    elif rotation == 270:
        rotate_filter = "transpose=2,"
    
    # 构建ffmpeg命令，保留原始分辨率但确保编码兼容
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-map_metadata", "-1",  # 移除全部元数据
        "-vf", f"{rotate_filter}format=yuv420p",  # 仅转换像素格式，不改变原分辨率
        "-c:v", "libx264",  # 使用H.264编码
        "-preset", "fast",  # 较快的编码速度
        "-crf", "23",  # 控制质量
        "-an",  # 不包含音频
        processed_path
    ]
    
    logger.info(f"执行转码命令: {' '.join(cmd)}")
    
    # 执行命令并显示进度
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, 
        universal_newlines=True
    )
    
    # 显示处理进度
    for line in process.stderr:
        if "time=" in line and "bitrate=" in line:
            logger.info(f"转码进度: {line.strip()}")
    
    process.wait()
    
    if process.returncode != 0 or not os.path.exists(processed_path):
        logger.error(f"视频转码失败")
        return video_path  # 转码失败时返回原始文件
    
    logger.success(f"视频修复完成: {os.path.basename(processed_path)}")
    return processed_path


def generate_video_ffmpeg(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    """使用纯ffmpeg实现视频生成，完全不依赖MoviePy"""
    logger.info(f"使用ffmpeg生成视频")
    aspect = VideoAspect(params.video_aspect)
    video_width, video_height = aspect.to_resolution()

    logger.info(f"视频尺寸: {video_width} x {video_height}")
    logger.info(f"视频: {video_path}")
    logger.info(f"音频: {audio_path}")
    logger.info(f"字幕: {subtitle_path}")
    logger.info(f"输出: {output_file}")

    # 创建临时目录
    temp_dir = os.path.join(os.path.dirname(output_file), f"temp_ffmpeg_{str(uuid.uuid4())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # 处理视频旋转和分辨率
        processed_video = os.path.join(temp_dir, "processed_video.mp4")
        rotation = get_video_rotation(video_path)
        rotate_filter = ""
        if rotation == 90:
            rotate_filter = "transpose=1,"
        elif rotation == 180:
            rotate_filter = "transpose=2,transpose=2,"
        elif rotation == 270:
            rotate_filter = "transpose=2,"
            
        video_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"{rotate_filter}scale={video_width}:{video_height}:force_original_aspect_ratio=increase,crop={video_width}:{video_height},format=yuv420p",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-an",  # 不包含音频
            "-map_metadata", "-1",  # 移除所有元数据
            processed_video
        ]
        
        logger.info("处理视频...")
        video_process = subprocess.Popen(
            video_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            universal_newlines=True
        )
        
        # 显示处理进度
        for line in video_process.stderr:
            if "time=" in line and "bitrate=" in line:
                logger.info(f"视频处理进度: {line.strip()}")
        
        video_process.wait()
        
        if video_process.returncode != 0 or not os.path.exists(processed_video):
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
            subprocess.run(subtitle_cmd, check=True, capture_output=True)
            
            if os.path.exists(ass_subtitle):
                # 安全处理路径中的特殊字符
                safe_subtitle_path = ass_subtitle.replace(":", "\\:")
                subtitle_filter = f"subtitles={safe_subtitle_path}:force_style='FontName={params.font_name},FontSize={params.font_size},PrimaryColour=&H{params.text_fore_color[1:]}&,OutlineColour=&H{params.stroke_color[1:]}&,BorderStyle=1,Outline={params.stroke_width},Alignment={alignment}'"
        
        # 音频处理
        merged_audio = os.path.join(temp_dir, "merged_audio.aac")
        
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
        subprocess.run(audio_cmd, check=True, capture_output=True)
        
        if not os.path.exists(merged_audio):
            logger.error("音频处理失败")
            return None
        
        # 最终合并视频、音频和字幕
        final_cmd = [
            "ffmpeg", "-y",
            "-i", processed_video,
            "-i", merged_audio
        ]
        
        # 添加滤镜
        filter_complex = []
        
        if subtitle_filter:
            filter_complex.append(subtitle_filter)
            
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
                logger.info(f"最终合成进度: {line.strip()}")
        
        final_process.wait()
        
        if final_process.returncode != 0 or not os.path.exists(output_file):
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
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.error(f"清理临时文件失败: {str(e)}")


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
                            "-show_entries", "stream=width,height,codec_name", "-of", "json", material.url]
                result = subprocess.run(probe_cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if "streams" in data and data["streams"]:
                        stream = data["streams"][0]
                        width = stream.get("width", 0)
                        height = stream.get("height", 0)
                        codec = stream.get("codec_name", "")
                        
                        logger.info(f"视频信息: 宽={width}, 高={height}, 编码={codec}")
                        
                        # 尺寸太小的视频跳过
                        if width < 480 or height < 480:
                            logger.warning(f"视频太小，宽: {width}, 高: {height}")
                            continue
                        
                        # 对HEVC编码视频或高分辨率视频进行转码
                        if "hevc" in codec.lower() or "h265" in codec.lower() or width > 1920 or height > 1920:
                            logger.info(f"需要转码的视频: {material.url}")
                            output_path = os.path.join(os.path.dirname(material.url), f"processed_{os.path.basename(material.url)}")
                            
                            # 转码命令
                            transcode_cmd = [
                                "ffmpeg", "-y",
                                "-i", material.url,
                                "-c:v", "libx264",
                                "-crf", "23",
                                "-preset", "fast",
                                "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p",
                                "-c:a", "aac",
                                "-b:a", "128k",
                                "-map_metadata", "-1",  # 移除所有元数据
                                output_path
                            ]
                            
                            subprocess.run(transcode_cmd, check=True, capture_output=True)
                            
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                material.url = output_path
                                logger.info(f"视频转码成功: {output_path}")
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

# 完全使用ffmpeg实现的combine_videos_ffmpeg函数
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
        logger.info(f"视频分辨率: {video_width}x{video_height}")
        
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
                video_probe_cmd = ["ffprobe", "-v", "error", "-show_entries", 
                                  "format=duration:stream=width,height,rotation,codec_name", 
                                  "-of", "json", video_path]
                video_info = json.loads(subprocess.check_output(video_probe_cmd, universal_newlines=True))
                
                video_duration = float(video_info["format"]["duration"])
                rotation = 0
                codec = ""
                
                for stream in video_info.get("streams", []):
                    if stream.get("codec_type") == "video":
                        # 获取旋转信息
                        if "tags" in stream and "rotate" in stream["tags"]:
                            rotation = int(stream["tags"]["rotate"])
                        elif "side_data_list" in stream:
                            for side_data in stream.get("side_data_list", []):
                                if side_data.get("rotation") is not None:
                                    rotation = int(side_data["rotation"])
                        # 获取编码信息
                        codec = stream.get("codec_name", "")
                        break
                
                logger.info(f"旋转: {rotation}°, 编码: {codec}, 时长: {video_duration}秒")
                
                # 确定裁剪点
                start_time = 0
                clip_count = 0
                
                while start_time < video_duration:
                    end_time = min(start_time + max_clip_duration, video_duration)
                    segment_duration = end_time - start_time
                    
                    # 如果片段太短，跳过
                    if segment_duration < 0.5:
                        logger.warning(f"片段太短 ({segment_duration}秒)，跳过")
                        start_time = end_time
                        continue
                    
                    # 创建输出文件名
                    segment_file = os.path.join(temp_dir, f"segment_{segment_index:04d}.mp4")
                    segment_index += 1
                    segment_files.append(segment_file)
                    
                    # 设置旋转滤镜
                    rotate_filter = ""
                    if rotation == 90:
                        rotate_filter = "transpose=1,"
                    elif rotation == 180:
                        rotate_filter = "transpose=2,transpose=2,"
                    elif rotation == 270:
                        rotate_filter = "transpose=2,"
                    
                    # 裁剪并处理视频
                    process_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(start_time),
                        "-i", video_path,
                        "-t", str(segment_duration),
                        "-vf", f"{rotate_filter}scale={video_width}:{video_height}:force_original_aspect_ratio=increase,crop={video_width}:{video_height},format=yuv420p",
                        "-an",  # 去除音频
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-map_metadata", "-1",  # 移除所有元数据
                        segment_file
                    ]
                    
                    logger.info(f"处理片段: {start_time}s - {end_time}s -> {os.path.basename(segment_file)}")
                    subprocess.run(process_cmd, check=True, capture_output=True)
                    
                    # 检查输出文件
                    if os.path.exists(segment_file) and os.path.getsize(segment_file) > 0:
                        processed_segments.append({
                            "file": segment_file,
                            "duration": segment_duration
                        })
                        clip_count += 1
                    else:
                        logger.error(f"生成片段失败: {segment_file}")
                        if os.path.exists(segment_file):
                            os.remove(segment_file)
                    
                    # 如果是顺序模式，只取一个片段
                    if video_concat_mode.value == VideoConcatMode.sequential.value:
                        break
                    
                    start_time = end_time
                
                logger.info(f"从视频中提取了 {clip_count} 个片段")
                
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
        subprocess.run(concat_cmd, check=True, capture_output=True)
        
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
        subprocess.run(final_cmd, check=True, capture_output=True)
        
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


if __name__ == "__main__":
    m = MaterialInfo()
    m.url = "/Users/harry/Downloads/IMG_2915.JPG"
    m.provider = "local"
    materials = preprocess_video([m], clip_duration=4)
    print(materials)

    # txt_en = "Here's your guide to travel hacks for budget-friendly adventures"
    # txt_zh = "测试长字段这是您的旅行技巧指南帮助您进行预算友好的冒险"
    # font = utils.resource_dir() + "/fonts/STHeitiMedium.ttc"
    # for txt in [txt_en, txt_zh]:
    #     t, h = wrap_text(text=txt, max_width=1000, font=font, fontsize=60)
    #     print(t)
    #
    # task_id = "aa563149-a7ea-49c2-b39f-8c32cc225baf"
    # task_dir = utils.task_dir(task_id)
    # video_file = f"{task_dir}/combined-1.mp4"
    # audio_file = f"{task_dir}/audio.mp3"
    # subtitle_file = f"{task_dir}/subtitle.srt"
    # output_file = f"{task_dir}/final.mp4"
    #
    # # video_paths = []
    # # for file in os.listdir(utils.storage_dir("test")):
    # #     if file.endswith(".mp4"):
    # #         video_paths.append(os.path.join(utils.storage_dir("test"), file))
    # #
    # # combine_videos(combined_video_path=video_file,
    # #                audio_file=audio_file,
    # #                video_paths=video_paths,
    # #                video_aspect=VideoAspect.portrait,
    # #                video_concat_mode=VideoConcatMode.random,
    # #                max_clip_duration=5,
    # #                threads=2)
    #
    # cfg = VideoParams()
    # cfg.video_aspect = VideoAspect.portrait
    # cfg.font_name = "STHeitiMedium.ttc"
    # cfg.font_size = 60
    # cfg.stroke_color = "#000000"
    # cfg.stroke_width = 1.5
    # cfg.text_fore_color = "#FFFFFF"
    # cfg.text_background_color = "transparent"
    # cfg.bgm_type = "random"
    # cfg.bgm_file = ""
    # cfg.bgm_volume = 1.0
    # cfg.subtitle_enabled = True
    # cfg.subtitle_position = "bottom"
    # cfg.n_threads = 2
    # cfg.paragraph_number = 1
    #
    # cfg.voice_volume = 1.0
    #
    # generate_video(video_path=video_file,
    #                audio_path=audio_file,
    #                subtitle_path=subtitle_file,
    #                output_file=output_file,
    #                params=cfg
    #                )
