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
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
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
                rotation = get_video_rotation(video_path)
                if rotation != 0:
                    logger.info(f"⚠️ 检测到视频旋转: {rotation}°, 将在处理过程中应用旋转矫正")
                else:
                    logger.info(f"视频旋转角度: {rotation}°")
                
                # 确定实际的视频方向
                actual_width = v_width
                actual_height = v_height
                
                # 考虑旋转后的实际方向
                if rotation in [90, 270, -90]:
                    actual_width, actual_height = actual_height, actual_width
                
                is_portrait = actual_height > actual_width
                logger.info(f"视频实际方向: {'竖屏' if is_portrait else '横屏'}, 实际尺寸: {actual_width}x{actual_height}")
                
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
                    rotate_filter = "transpose=1,"  # 顺时针旋转90度
                    logger.info("应用90度顺时针旋转滤镜")
                elif rotation == 270 or rotation == -90:
                    rotate_filter = "transpose=2,"  # 逆时针旋转90度（等于顺时针旋转270度）
                    logger.info("应用270度顺时针旋转滤镜（逆时针90度）")
                elif rotation == 180:
                    rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
                    logger.info("应用180度旋转滤镜")
                
                # 处理HEVC编码的视频
                if codec_name.lower() == 'hevc':
                    # 先进行转码处理
                    hevc_output = os.path.join(temp_dir, f"hevc_converted_{idx:03d}.mp4")
                    
                    # 使用更强大的处理参数
                    hevc_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(remaining_duration),
                        "-i", video_path,
                        "-t", str(clip_duration),
                        "-map_metadata", "-1",  # 移除所有元数据
                        "-vf", f"{rotate_filter}format=yuv420p",  # 只旋转，不缩放
                        "-c:v", "libx264",
                        "-crf", "23",
                        "-preset", "medium",
                        "-pix_fmt", "yuv420p",
                        "-color_primaries", "bt709",
                        "-color_trc", "bt709",
                        "-colorspace", "bt709",
                        "-movflags", "+faststart",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-max_muxing_queue_size", "9999",
                        hevc_output
                    ]
                    
                    logger.info(f"处理HEVC视频: {os.path.basename(video_path)}, 从{remaining_duration}秒开始, 时长{clip_duration}秒")
                    hevc_result = subprocess.run(hevc_cmd, capture_output=True, text=True)
                    
                    if hevc_result.returncode != 0:
                        logger.error(f"HEVC转码失败: {hevc_result.stderr}")
                        continue
                        
                    # 使用转码后的视频
                    process_cmd = [
                        "ffmpeg", "-y",
                        "-i", hevc_output,
                        "-c", "copy",  # 直接复制，不重新编码
                        "-t", str(clip_duration),
                        segment_path
                    ]
                else:
                    # 普通视频直接处理
                    process_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(remaining_duration),
                        "-i", video_path,
                        "-t", str(clip_duration),
                        "-vf", f"{rotate_filter}format=yuv420p",
                        "-an",  # 去除音频
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "23",
                        "-map_metadata", "-1",  # 移除所有元数据
                        segment_path
                    ]
                
                logger.info(f"创建视频片段: {segment_filename}, 时长: {clip_duration:.2f}秒")
                segment_result = subprocess.run(process_cmd, capture_output=True, text=True)
                
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
    """修复视频以便MoviePy库可以读取，主要是旋转和编码格式问题"""
    if not os.path.exists(video_path):
        logger.error(f"视频文件不存在: {video_path}")
        return video_path
    
    try:
        # 检查编解码器是否需要转换
        video_codec = get_video_codec(video_path)
        rotation = get_video_rotation(video_path)
        
        # 检测可用的硬件加速
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
        
        # 如果不是H.264或存在旋转，则需要修复
        if (force_h264 and video_codec != "h264") or rotation > 0:
            logger.info(f"需要修复视频: {os.path.basename(video_path)}")
            logger.info(f"当前编码: {video_codec}, 旋转角度: {rotation}")
            
            # 创建临时文件名，保持在同一目录减少IO
            file_dir = os.path.dirname(video_path)
            base_name = os.path.basename(video_path)
            name, ext = os.path.splitext(base_name)
            processed_path = os.path.join(file_dir, f"{name}_fixed.mp4")
            
            # 旋转滤镜
            rotate_filter = ""
            if rotation == 90:
                rotate_filter = "transpose=1,"
            elif rotation == 180:
                rotate_filter = "transpose=2,transpose=2,"
            elif rotation == 270:
                rotate_filter = "transpose=2,"
            
            # FFmpeg命令
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", f"{rotate_filter}format=yuv420p",
                "-c:v", hw_accel
            ]
            
            # 根据编码器添加特定参数
            if hw_accel == "libx264":
                cmd.extend([
                    "-preset", "medium",
                    "-crf", "23"
                ])
            else:
                cmd.extend([
                    "-preset", "p1"
                ])
            
            # 添加其他参数
            cmd.extend([
                "-c:a", "copy",
                "-movflags", "+faststart",
                processed_path
            ])
            
            logger.info(f"修复视频中...")
            
            # 使用Popen捕获输出
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                universal_newlines=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 收集错误输出
            stderr_output = []
            
            # 显示处理进度
            for line in process.stderr:
                stderr_output.append(line)
                if "time=" in line and "bitrate=" in line:
                    logger.info(f"转码进度: {line.strip()}")
            
            process.wait()
            
            if process.returncode != 0 or not os.path.exists(processed_path):
                logger.error(f"视频转码失败，错误详情:")
                for line in stderr_output:
                    logger.error(line.strip())
                return video_path  # 转码失败时返回原始文件
            
            logger.success(f"视频修复完成: {os.path.basename(processed_path)}")
            return processed_path
        else:
            logger.info(f"视频无需修复: {os.path.basename(video_path)}")
            return video_path
    except Exception as e:
        logger.error(f"视频修复过程中出错: {str(e)}")
        return video_path


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
    max_width, max_height = aspect.to_resolution()

    logger.info(f"最大视频尺寸: {max_width} x {max_height}")
    logger.info(f"视频: {video_path}")
    logger.info(f"音频: {audio_path}")
    logger.info(f"字幕: {subtitle_path}")
    logger.info(f"输出: {output_file}")

    # 创建临时目录
    temp_dir = os.path.join(os.path.dirname(output_file), f"temp_ffmpeg_{str(uuid.uuid4())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # 检测可用的硬件加速
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
        
        # 获取视频信息，检查是否需要处理
        video_metadata_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", 
                               "-show_entries", "stream=width,height,codec_name", 
                               "-of", "json", video_path]
        video_info = json.loads(subprocess.check_output(video_metadata_cmd, universal_newlines=True))
        
        original_width = 0
        original_height = 0
        original_codec = ""
        
        # 获取原始视频信息
        if "streams" in video_info and video_info["streams"]:
            stream = video_info["streams"][0]
            original_width = int(stream.get("width", 0))
            original_height = int(stream.get("height", 0))
            original_codec = stream.get("codec_name", "").lower()
            
        logger.info(f"原始视频信息 - 宽: {original_width}, 高: {original_height}, 编码: {original_codec}")
        
        # 处理视频旋转和分辨率
        processed_video = os.path.join(temp_dir, "processed_video.mp4")
        rotation = get_video_rotation(video_path)
        if rotation != 0:
            logger.info(f"⚠️ 检测到视频需要旋转 {rotation}° 度")
        else:
            logger.info(f"视频旋转检测值: {rotation}°")
        
        # 检查是否需要旋转
        is_portrait_orientation = original_height > original_width
        target_is_portrait = max_height > max_width
        
        # 特殊情况：竖屏拍摄但宽高比例是横屏
        needs_rotation_fix = False
        if target_is_portrait and not is_portrait_orientation and rotation == 0:
            # 检查宽高比是否接近16:9
            aspect_ratio = original_width / original_height if original_height > 0 else 0
            if 1.7 < aspect_ratio < 1.8:  # 接近16:9
                logger.info("检测到可能是竖屏视频被记录为横屏，考虑旋转90度")
                needs_rotation_fix = True
        
        # 设置旋转滤镜
        rotate_filter = ""
        if rotation == 90 or needs_rotation_fix:
            rotate_filter = "transpose=1,"
        elif rotation == 180:
            rotate_filter = "transpose=2,transpose=2,"
        elif rotation == 270:
            rotate_filter = "transpose=2,"
        
        # 确定是否需要进行尺寸调整
        needs_resize = False
        final_width = original_width
        final_height = original_height
        
        # 旋转后的尺寸
        if rotate_filter:
            final_width, final_height = final_height, final_width
        
        # 检查是否超过最大分辨率限制
        if final_width > max_width or final_height > max_height:
            needs_resize = True
            logger.info(f"视频尺寸超过限制，需要调整")
        else:
            logger.info(f"视频尺寸在限制范围内，保持原始尺寸")
        
        # 设置缩放参数
        scale_filter = ""
        if needs_resize:
            scale_filter = f"scale={max_width}:{max_height}:force_original_aspect_ratio=decrease,pad={max_width}:{max_height}:(ow-iw)/2:(oh-ih)/2"
        else:
            if rotate_filter:  # 仅旋转时可能需要设置输出分辨率
                scale_filter = f"scale={final_width}:{final_height}"
        
        # 确定是否需要处理视频
        needs_processing = rotation != 0 or needs_rotation_fix or needs_resize or original_codec != "h264"
        
        if needs_processing:
            # 构建完整的视频滤镜
            full_filter = ""
            if rotate_filter and scale_filter:
                full_filter = f"{rotate_filter}{scale_filter}"
            elif rotate_filter:
                full_filter = rotate_filter.rstrip(",")
            elif scale_filter:
                full_filter = scale_filter
            
            # 添加像素格式转换确保兼容性
            if full_filter:
                full_filter += ",format=yuv420p"
            else:
                full_filter = "format=yuv420p"
            
            video_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", full_filter,
                "-c:v", hw_accel,
                "-preset", "medium" if hw_accel == "libx264" else "p1",
                "-crf", "23" if hw_accel == "libx264" else None,
                "-an",  # 不包含音频
                "-map_metadata", "-1",  # 移除所有元数据
                processed_video
            ]
            
            # 移除None值
            video_cmd = [item for item in video_cmd if item is not None]
            
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
                    
                    # 构建字幕滤镜
                    subtitle_filter = f"subtitles={safe_subtitle_path}:force_style='FontName={safe_font_name},FontSize={params.font_size},PrimaryColour=&H{params.text_fore_color[1:]}&,OutlineColour=&H{params.stroke_color[1:]}&,BorderStyle=1,Outline={params.stroke_width},Alignment={alignment}'"
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
                        subtitle_filter = f"subtitles='{raw_path}':force_style='FontName={params.font_name},FontSize={params.font_size},Alignment={alignment}'"
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
                            
                            # 特殊情况处理：竖屏拍摄但分辨率是横屏
                            if not is_portrait and rotation == 0 and 1.7 < (width / height) < 1.8:
                                # 添加90度旋转
                                rotate_filter = "transpose=1,"
                                # 交换宽高
                                width, height = height, width
                            
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
                            
                            scale_filter = f"scale={target_width}:{target_height}"
                            
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
                            
                            # 转码命令
                            transcode_cmd = [
                                "ffmpeg", "-y",
                                "-i", material.url,
                                "-c:v", "libx264",
                                "-crf", "23",
                                "-preset", "fast",
                                "-vf", vf_filter,
                                "-c:a", "aac",
                                "-b:a", "128k",
                                "-map_metadata", "-1",  # 移除所有元数据
                                "-movflags", "+faststart",  # 添加快速启动标志
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
                    
                    # 获取旋转信息
                    rotation = get_video_rotation(video_path)
                    logger.info(f"视频旋转角度: {rotation}°")
                    
                    # 考虑旋转后的实际方向
                    if rotation in [90, 270, -90]:
                        v_width, v_height = v_height, v_width
                    
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
                        
                        # 构建旋转滤镜
                        rotate_filter = ""
                        if rotation == 90:
                            rotate_filter = "transpose=1,"  # 顺时针旋转90度
                        elif rotation == 180:
                            rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
                        elif rotation == 270 or rotation == -90:
                            rotate_filter = "transpose=2,"  # 逆时针旋转90度
                        
                        # 根据视频方向和目标方向设置缩放参数
                        scale_filter = ""
                        if is_portrait:
                            # 竖屏视频
                            if aspect == VideoAspect.portrait:
                                # 目标也是竖屏，保持比例
                                scale_filter = "scale=1080:-2"
                            else:
                                # 目标是横屏，需要确保不裁剪内容
                                scale_filter = "scale=-2:1080"
                        else:
                            # 横屏视频
                            if aspect == VideoAspect.landscape:
                                # 目标也是横屏，保持比例
                                scale_filter = "scale=1920:-2"
                            else:
                                # 目标是竖屏，需要确保不裁剪内容
                                scale_filter = "scale=-2:1920"
                        
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
                                "-vf", f"{rotate_filter}format=yuv420p",
                                "-c:v", "libx264",
                                "-crf", "23",
                                "-preset", "medium",
                                "-pix_fmt", "yuv420p",
                                "-color_primaries", "bt709",
                                "-color_trc", "bt709",
                                "-colorspace", "bt709",
                                "-movflags", "+faststart",
                                "-an",  # 不包含音频
                                "-max_muxing_queue_size", "9999",
                                hevc_output
                            ]
                            
                            logger.info(f"处理HEVC视频: {os.path.basename(video_path)}, 从{start_time}秒开始, 时长{segment_duration}秒")
                            hevc_result = subprocess.run(hevc_cmd, capture_output=True, text=True)
                            
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
                            
                            copy_result = subprocess.run(copy_cmd, capture_output=True, text=True)
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
                                "-vf", f"{rotate_filter}format=yuv420p",
                                "-an",  # 去除音频
                                "-c:v", "libx264",
                                "-preset", "medium",
                                "-crf", "23",
                                "-map_metadata", "-1",  # 移除所有元数据
                                segment_path
                            ]
                            
                            logger.info(f"处理普通视频片段: {os.path.basename(video_path)}, 从{start_time}秒开始, 时长{segment_duration}秒")
                            segment_result = subprocess.run(process_cmd, capture_output=True, text=True)
                            
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
