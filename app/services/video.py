import glob
import os
import random
from typing import List
import time
import re
import json
import subprocess
import shutil  # 添加shutil导入
import uuid

from loguru import logger
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
)
from moviepy.video.tools.subtitles import SubtitlesClip
from PIL import ImageFont

from app.models import const
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.services.utils import video_effects
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
    processed_paths = []  # 用于记录需要清理的临时文件
    
    try:
        # 首先检查所有文件是否存在
        if not video_paths:
            logger.error("没有输入视频文件")
            return None
            
        if not os.path.exists(audio_file):
            logger.error(f"音频文件不存在: {audio_file}")
            return None
            
        # 预处理视频列表，处理HEVC编码和旋转问题
        processed_video_paths = []
        
        for idx, video_path in enumerate(video_paths):
            try:
                if not os.path.exists(video_path):
                    logger.error(f"视频文件不存在: {video_path}")
                    continue
                    
                # 获取视频信息
                rotation = get_video_rotation(video_path)
                codec = get_video_codec(video_path)
                logger.info(f"处理视频 {idx+1}/{len(video_paths)}: {os.path.basename(video_path)}")
                logger.info(f"旋转: {rotation}°, 编码: {codec}")
                
                # 对HEVC编码视频进行预处理
                if "hevc" in codec.lower() or "h265" in codec.lower():
                    processed_path = preprocess_hevc_video(video_path)
                    if processed_path != video_path:
                        processed_paths.append(processed_path)
                        video_path = processed_path
                        logger.info(f"使用预处理后的视频: {os.path.basename(video_path)}")
                
                # 检查旋转角度
                if rotation in [90, 270, 180]:
                    fixed_path = fix_video_for_moviepy(video_path, rotation=rotation)
                    if fixed_path != video_path:
                        processed_paths.append(fixed_path)
                        video_path = fixed_path
                        logger.info(f"使用旋转修正后的视频: {os.path.basename(video_path)}")
                
                processed_video_paths.append(video_path)
                        
            except Exception as e:
                logger.error(f"视频处理失败，将跳过: {os.path.basename(video_path)}, 错误: {str(e)}")
                continue
        
        # 如果没有可用的视频，直接返回
        if not processed_video_paths:
            logger.error("没有可用的视频，合成失败")
            return None
            
        # 使用原始的MoviePy逻辑处理视频
        video_paths = processed_video_paths
        
        # 以下是原始的combine_videos函数逻辑
        audio_clip = AudioFileClip(audio_file)
        audio_duration = audio_clip.duration
        logger.info(f"max duration of audio: {audio_duration} seconds")
        # Required duration of each clip
        req_dur = audio_duration / len(video_paths)
        req_dur = max_clip_duration
        logger.info(f"each clip will be maximum {req_dur} seconds long")
        output_dir = os.path.dirname(combined_video_path)

        aspect = VideoAspect(video_aspect)
        video_width, video_height = aspect.to_resolution()

        clips = []
        video_duration = 0

        raw_clips = []
        for video_path in video_paths:
            try:
                clip = VideoFileClip(video_path).without_audio()
                clip_duration = clip.duration
                start_time = 0

                while start_time < clip_duration:
                    end_time = min(start_time + max_clip_duration, clip_duration)
                    split_clip = clip.subclipped(start_time, end_time)
                    raw_clips.append(split_clip)
                    start_time = end_time
                    if video_concat_mode.value == VideoConcatMode.sequential.value:
                        break
            except Exception as e:
                logger.error(f"读取视频失败: {video_path}, 错误: {str(e)}")
                continue

        # random video_paths order
        if video_concat_mode.value == VideoConcatMode.random.value:
            random.shuffle(raw_clips)

        # Add downloaded clips over and over until the duration of the audio (max_duration) has been reached
        while video_duration < audio_duration and raw_clips:
            for clip in raw_clips[:]:  # 使用副本遍历，避免修改迭代中的列表
                if not raw_clips:  # 如果列表已空，退出循环
                    break
                    
                # Check if clip is longer than the remaining audio
                if (audio_duration - video_duration) < clip.duration:
                    clip = clip.subclipped(0, (audio_duration - video_duration))
                # Only shorten clips if the calculated clip length (req_dur) is shorter than the actual clip to prevent still image
                elif req_dur < clip.duration:
                    clip = clip.subclipped(0, req_dur)
                clip = clip.with_fps(30)

                # Not all videos are same size, so we need to resize them
                clip_w, clip_h = clip.size
                if clip_w != video_width or clip_h != video_height:
                    clip_ratio = clip.w / clip.h
                    video_ratio = video_width / video_height

                    if clip_ratio == video_ratio:
                        # Resize proportionally
                        clip = clip.resized((video_width, video_height))
                    else:
                        # Resize proportionally
                        if clip_ratio > video_ratio:
                            # Resize proportionally based on the target width
                            scale_factor = video_width / clip_w
                        else:
                            # Resize proportionally based on the target height
                            scale_factor = video_height / clip_h

                        new_width = int(clip_w * scale_factor)
                        new_height = int(clip_h * scale_factor)
                        clip_resized = clip.resized(new_size=(new_width, new_height))

                        background = ColorClip(
                            size=(video_width, video_height), color=(0, 0, 0)
                        )
                        clip = CompositeVideoClip(
                            [
                                background.with_duration(clip.duration),
                                clip_resized.with_position("center"),
                            ]
                        )

                    logger.info(
                        f"resizing video to {video_width} x {video_height}, clip size: {clip_w} x {clip_h}"
                    )

                shuffle_side = random.choice(["left", "right", "top", "bottom"])
                logger.info(f"Using transition mode: {video_transition_mode}")
                if video_transition_mode is None or video_transition_mode.value == VideoTransitionMode.none.value:
                    clip = clip
                elif video_transition_mode.value == VideoTransitionMode.fade_in.value:
                    clip = video_effects.fadein_transition(clip, 1)
                elif video_transition_mode.value == VideoTransitionMode.fade_out.value:
                    clip = video_effects.fadeout_transition(clip, 1)
                elif video_transition_mode.value == VideoTransitionMode.slide_in.value:
                    clip = video_effects.slidein_transition(clip, 1, shuffle_side)
                elif video_transition_mode.value == VideoTransitionMode.slide_out.value:
                    clip = video_effects.slideout_transition(clip, 1, shuffle_side)
                elif video_transition_mode.value == VideoTransitionMode.shuffle.value:
                    transition_funcs = [
                        lambda c: video_effects.fadein_transition(c, 1),
                        lambda c: video_effects.fadeout_transition(c, 1),
                        lambda c: video_effects.slidein_transition(c, 1, shuffle_side),
                        lambda c: video_effects.slideout_transition(c, 1, shuffle_side),
                    ]
                    shuffle_transition = random.choice(transition_funcs)
                    clip = shuffle_transition(clip)

                if clip.duration > max_clip_duration:
                    clip = clip.subclipped(0, max_clip_duration)

                clips.append(clip)
                video_duration += clip.duration
                
                # 如果已达到所需时长，跳出循环
                if video_duration >= audio_duration:
                    break
                    
        clips = [CompositeVideoClip([clip]) for clip in clips]
        video_clip = concatenate_videoclips(clips)
        video_clip = video_clip.with_fps(30)
        logger.info("开始写入视频文件")
        
        # https://github.com/harry0703/MoneyPrinterTurbo/issues/111#issuecomment-2032354030
        video_clip.write_videofile(
            filename=combined_video_path,
            threads=threads,
            logger=None,
            temp_audiofile_path=output_dir,
            audio_codec="aac",
            fps=30,
        )
        video_clip.close()
        logger.success("合成完成")
        return combined_video_path
        
    except Exception as e:
        logger.error(f"视频合成过程失败: {str(e)}", exc_info=True)
        
        # 如果MoviePy处理失败，尝试使用ffmpeg
        try:
            logger.warning("尝试使用ffmpeg方案继续处理")
            return combine_videos_with_ffmpeg(
                combined_video_path=combined_video_path,
                video_paths=processed_video_paths or video_paths,
                audio_file=audio_file,
                video_aspect=video_aspect,
                max_clip_duration=max_clip_duration,
                threads=threads
            )
        except Exception as e2:
            logger.error(f"ffmpeg处理也失败: {str(e2)}")
            return None
    finally:
        # 清理临时文件
        try:
            for temp_path in processed_paths:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    logger.info(f"已删除临时处理文件: {temp_path}")
        except Exception as e:
            logger.warning(f"清理临时文件失败: {str(e)}")

# 辅助函数，用于调整视频大小以适应目标宽高比
def resize_clip_to_aspect(clip, target_width, target_height):
    clip_w, clip_h = clip.size
    
    if clip_w != target_width or clip_h != target_height:
        clip_ratio = clip.w / clip.h
        target_ratio = target_width / target_height

        if clip_ratio == target_ratio:
            # 直接调整大小
            return clip.resized((target_width, target_height))
        else:
            # 按比例调整大小，保持原始宽高比
            if clip_ratio > target_ratio:
                # 基于目标宽度调整大小
                scale_factor = target_width / clip_w
            else:
                # 基于目标高度调整大小
                scale_factor = target_height / clip_h

            new_width = int(clip_w * scale_factor)
            new_height = int(clip_h * scale_factor)
            clip_resized = clip.resized(new_size=(new_width, new_height))

            # 创建背景
            background = ColorClip(
                size=(target_width, target_height), color=(0, 0, 0)
            )
            
            # 创建复合视频剪辑
            return CompositeVideoClip(
                [
                    background.with_duration(clip.duration),
                    clip_resized.with_position("center"),
                ]
            )
    
    return clip

# 辅助函数，应用过渡效果
def apply_transition(clip, transition_mode):
    shuffle_side = random.choice(["left", "right", "top", "bottom"])
    
    if transition_mode.value == VideoTransitionMode.fade_in.value:
        return video_effects.fadein_transition(clip, 1)
    elif transition_mode.value == VideoTransitionMode.fade_out.value:
        return video_effects.fadeout_transition(clip, 1)
    elif transition_mode.value == VideoTransitionMode.slide_in.value:
        return video_effects.slidein_transition(clip, 1, shuffle_side)
    elif transition_mode.value == VideoTransitionMode.slide_out.value:
        return video_effects.slideout_transition(clip, 1, shuffle_side)
    elif transition_mode.value == VideoTransitionMode.shuffle.value:
        transition_funcs = [
            lambda c: video_effects.fadein_transition(c, 1),
            lambda c: video_effects.fadeout_transition(c, 1),
            lambda c: video_effects.slidein_transition(c, 1, shuffle_side),
            lambda c: video_effects.slideout_transition(c, 1, shuffle_side),
        ]
        shuffle_transition = random.choice(transition_funcs)
        return shuffle_transition(clip)
    
    return clip

def wrap_text(text, max_width, font="Arial", fontsize=60):
    # Create ImageFont
    font = ImageFont.truetype(font, fontsize)

    def get_text_size(inner_text):
        inner_text = inner_text.strip()
        left, top, right, bottom = font.getbbox(inner_text)
        return right - left, bottom - top

    width, height = get_text_size(text)
    if width <= max_width:
        return text, height

    # logger.warning(f"wrapping text, max_width: {max_width}, text_width: {width}, text: {text}")

    processed = True

    _wrapped_lines_ = []
    words = text.split(" ")
    _txt_ = ""
    for word in words:
        _before = _txt_
        _txt_ += f"{word} "
        _width, _height = get_text_size(_txt_)
        if _width <= max_width:
            continue
        else:
            if _txt_.strip() == word.strip():
                processed = False
                break
            _wrapped_lines_.append(_before)
            _txt_ = f"{word} "
    _wrapped_lines_.append(_txt_)
    if processed:
        _wrapped_lines_ = [line.strip() for line in _wrapped_lines_]
        result = "\n".join(_wrapped_lines_).strip()
        height = len(_wrapped_lines_) * height
        # logger.warning(f"wrapped text: {result}")
        return result, height

    _wrapped_lines_ = []
    chars = list(text)
    _txt_ = ""
    for word in chars:
        _txt_ += word
        _width, _height = get_text_size(_txt_)
        if _width <= max_width:
            continue
        else:
            _wrapped_lines_.append(_txt_)
            _txt_ = ""
    _wrapped_lines_.append(_txt_)
    result = "\n".join(_wrapped_lines_).strip()
    height = len(_wrapped_lines_) * height
    # logger.warning(f"wrapped text: {result}")
    return result, height


def generate_video(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    aspect = VideoAspect(params.video_aspect)
    video_width, video_height = aspect.to_resolution()

    logger.info(f"start, video size: {video_width} x {video_height}")
    logger.info(f"  ① video: {video_path}")
    logger.info(f"  ② audio: {audio_path}")
    logger.info(f"  ③ subtitle: {subtitle_path}")
    logger.info(f"  ④ output: {output_file}")

    # https://github.com/harry0703/MoneyPrinterTurbo/issues/217
    # PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: 'final-1.mp4.tempTEMP_MPY_wvf_snd.mp3'
    # write into the same directory as the output file
    output_dir = os.path.dirname(output_file)

    font_path = ""
    if params.subtitle_enabled:
        if not params.font_name:
            params.font_name = "STHeitiMedium.ttc"
        font_path = os.path.join(utils.font_dir(), params.font_name)
        if os.name == "nt":
            font_path = font_path.replace("\\", "/")

        logger.info(f"using font: {font_path}")

    def create_text_clip(subtitle_item):
        params.font_size = int(params.font_size)
        params.stroke_width = int(params.stroke_width)
        phrase = subtitle_item[1]
        max_width = video_width * 0.9
        wrapped_txt, txt_height = wrap_text(
            phrase, max_width=max_width, font=font_path, fontsize=params.font_size
        )
        _clip = TextClip(
            text=wrapped_txt,
            font=font_path,
            font_size=params.font_size,
            color=params.text_fore_color,
            bg_color=params.text_background_color,
            stroke_color=params.stroke_color,
            stroke_width=params.stroke_width,
        )
        duration = subtitle_item[0][1] - subtitle_item[0][0]
        _clip = _clip.with_start(subtitle_item[0][0])
        _clip = _clip.with_end(subtitle_item[0][1])
        _clip = _clip.with_duration(duration)
        if params.subtitle_position == "bottom":
            _clip = _clip.with_position(("center", video_height * 0.95 - _clip.h))
        elif params.subtitle_position == "top":
            _clip = _clip.with_position(("center", video_height * 0.05))
        elif params.subtitle_position == "custom":
            # Ensure the subtitle is fully within the screen bounds
            margin = 10  # Additional margin, in pixels
            max_y = video_height - _clip.h - margin
            min_y = margin
            custom_y = (video_height - _clip.h) * (params.custom_position / 100)
            custom_y = max(
                min_y, min(custom_y, max_y)
            )  # Constrain the y value within the valid range
            _clip = _clip.with_position(("center", custom_y))
        else:  # center
            _clip = _clip.with_position(("center", "center"))
        return _clip

    # 尝试检查视频是否仍然包含side data
    try:
        check_cmd = ["ffprobe", "-v", "error", "-show_streams", "-of", "json", video_path]
        check_result = subprocess.run(check_cmd, capture_output=True, text=True)
        
        if check_result.returncode == 0:
            data = json.loads(check_result.stdout)
            has_side_data = False
            
            for stream in data.get("streams", []):
                if "side_data_list" in stream:
                    has_side_data = True
                    for side_data in stream.get("side_data_list", []):
                        if side_data.get("side_data_type") == "Ambient Viewing Environment":
                            logger.warning(f"⚠️ 视频包含Ambient Viewing Environment元数据，将使用ffmpeg处理")
                            return generate_video_with_ffmpeg(video_path, audio_path, subtitle_path, output_file, params)
            
            if has_side_data:
                logger.warning(f"⚠️ 视频包含side data，可能会导致MoviePy处理失败")
    except Exception as e:
        logger.warning(f"检查side data时出错: {str(e)}")

    try:
        # 尝试使用MoviePy打开视频文件，验证是否可以被正常读取
        video_clip = VideoFileClip(video_path)
        audio_clip = AudioFileClip(audio_path).with_effects(
            [afx.MultiplyVolume(params.voice_volume)]
        )

        def make_textclip(text):
            return TextClip(
                text=text,
                font=font_path,
                font_size=params.font_size,
            )

        if subtitle_path and os.path.exists(subtitle_path):
            sub = SubtitlesClip(
                subtitles=subtitle_path, encoding="utf-8", make_textclip=make_textclip
            )
            text_clips = []
            for item in sub.subtitles:
                clip = create_text_clip(subtitle_item=item)
                text_clips.append(clip)
            video_clip = CompositeVideoClip([video_clip, *text_clips])

        bgm_file = get_bgm_file(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
        if bgm_file:
            try:
                bgm_clip = AudioFileClip(bgm_file).with_effects(
                    [
                        afx.MultiplyVolume(params.bgm_volume),
                        afx.AudioFadeOut(3),
                        afx.AudioLoop(duration=video_clip.duration),
                    ]
                )
                audio_clip = CompositeAudioClip([audio_clip, bgm_clip])
            except Exception as e:
                logger.error(f"failed to add bgm: {str(e)}")

        video_clip = video_clip.with_audio(audio_clip)
        
        video_clip.write_videofile(
            output_file,
            audio_codec="aac",
            temp_audiofile_path=output_dir,
            threads=params.n_threads or 2,
            logger=None,
            fps=30,
        )
        video_clip.close()
        del video_clip
        logger.success("completed")
        return output_file
        
    except Exception as e:
        logger.error(f"MoviePy处理视频失败: {str(e)}")
        # 尝试使用ffmpeg方法生成视频
        logger.warning("尝试使用ffmpeg方法生成视频")
        return generate_video_with_ffmpeg(video_path, audio_path, subtitle_path, output_file, params)


def generate_video_with_ffmpeg(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    """使用纯ffmpeg方法生成视频，作为MoviePy失败时的后备方案"""
    logger.info("使用纯ffmpeg方法处理视频")
    aspect = VideoAspect(params.video_aspect)
    video_width, video_height = aspect.to_resolution()
    
    # 创建临时目录
    temp_dir = os.path.join(os.path.dirname(output_file), "temp_ffmpeg_" + str(uuid.uuid4()))
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # 修正视频
        temp_video = os.path.join(temp_dir, "processed_video.mp4")
        rotate_filter = ""
        rotation = get_video_rotation(video_path)
        if rotation == 90:
            rotate_filter = "transpose=1,"
        elif rotation == 180:
            rotate_filter = "transpose=2,transpose=2,"
        elif rotation == 270:
            rotate_filter = "transpose=2,"
            
        # 设置视频处理命令
        video_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-map_metadata", "-1",  # 移除所有元数据
            "-vf", f"{rotate_filter}scale={video_width}:{video_height},format=yuv420p",
            "-c:v", "libx264",
            "-preset", "medium",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            temp_video
        ]
        
        logger.info(f"执行视频处理命令: {' '.join(video_cmd)}")
        video_process = subprocess.Popen(
            video_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            universal_newlines=True
        )
        
        # 实时显示进度
        for line in video_process.stderr:
            if "time=" in line and "bitrate=" in line:
                logger.info(f"视频处理进度: {line.strip()}")
        
        video_process.wait()
        
        if video_process.returncode != 0:
            logger.error(f"视频处理失败，返回码: {video_process.returncode}")
            return None
        
        logger.info("视频处理完成，准备合并音频")
        
        # 获取音频和视频的时长
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", audio_path]
        audio_duration_str = subprocess.check_output(probe_cmd, universal_newlines=True)
        audio_duration = float(json.loads(audio_duration_str)["format"]["duration"])
        
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", temp_video]
        video_duration_str = subprocess.check_output(probe_cmd, universal_newlines=True)
        video_duration = float(json.loads(video_duration_str)["format"]["duration"])
        
        # 准备BGM
        temp_bgm = None
        if params.bgm_type and params.bgm_type != "none":
            bgm_file = get_bgm_file(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
            if bgm_file:
                try:
                    # 调整BGM音量并循环
                    temp_bgm = os.path.join(temp_dir, "bgm_processed.mp3")
                    bgm_cmd = [
                        "ffmpeg", "-y",
                        "-i", bgm_file,
                        "-af", f"volume={params.bgm_volume},afade=t=out:st={audio_duration-3}:d=3",
                        "-to", str(audio_duration),
                        temp_bgm
                    ]
                    subprocess.run(bgm_cmd, check=True)
                    logger.info("BGM处理完成")
                except Exception as e:
                    logger.error(f"BGM处理失败: {str(e)}")
                    temp_bgm = None
        
        # 准备字幕文件(如果有)
        temp_subtitle = None
        if subtitle_path and os.path.exists(subtitle_path) and params.subtitle_enabled:
            try:
                # 转换字幕为ASS格式
                temp_subtitle = os.path.join(temp_dir, "subtitle.ass")
                font_path = ""
                if params.font_name:
                    font_path = os.path.join(utils.font_dir(), params.font_name)
                
                # 确定字幕位置
                line_position = 10  # 默认位置(底部)
                if params.subtitle_position == "top":
                    line_position = 90
                elif params.subtitle_position == "center":
                    line_position = 50
                elif params.subtitle_position == "custom":
                    line_position = params.custom_position
                
                subtitle_cmd = [
                    "ffmpeg", "-y",
                    "-i", subtitle_path,
                    "-c:s", "ass",
                    temp_subtitle
                ]
                subprocess.run(subtitle_cmd, check=True)
                logger.info("字幕转换完成")
            except Exception as e:
                logger.error(f"字幕处理失败: {str(e)}")
                temp_subtitle = None
        
        # 合并视频和音频(以及字幕和BGM)
        final_cmd = [
            "ffmpeg", "-y",
            "-i", temp_video,
            "-i", audio_path
        ]
        
        # 添加BGM输入(如果有)
        if temp_bgm:
            final_cmd.extend(["-i", temp_bgm])
        
        # 音频映射和混合
        if temp_bgm:
            final_cmd.extend([
                "-filter_complex", f"[1:a]volume={params.voice_volume}[a1];[2:a]volume={params.bgm_volume}[a2];[a1][a2]amix=inputs=2:duration=longest[a]",
                "-map", "0:v", "-map", "[a]"
            ])
        else:
            final_cmd.extend([
                "-filter_complex", f"[1:a]volume={params.voice_volume}[a]",
                "-map", "0:v", "-map", "[a]"
            ])
        
        # 添加字幕(如果有)
        if temp_subtitle:
            # 需要处理字幕路径中的特殊字符
            safe_subtitle_path = temp_subtitle.replace(":", "\\:")
            final_cmd.extend(["-vf", f"subtitles={safe_subtitle_path}"])
        
        # 输出设置
        final_cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-profile:v", "main",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            output_file
        ])
        
        logger.info(f"执行最终合并命令: {' '.join(final_cmd)}")
        final_process = subprocess.Popen(
            final_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            universal_newlines=True
        )
        
        # 实时显示进度
        for line in final_process.stderr:
            if "time=" in line and "bitrate=" in line:
                logger.info(f"合并进度: {line.strip()}")
        
        final_process.wait()
        
        if final_process.returncode != 0:
            logger.error(f"视频合并失败，返回码: {final_process.returncode}")
            return None
        
        logger.success("视频生成完成")
        return output_file
        
    except Exception as e:
        logger.error(f"使用ffmpeg生成视频时出错: {str(e)}")
        return None
    finally:
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.error(f"清理临时文件失败: {str(e)}")


def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
    for material in materials:
        if not material.url:
            continue

        ext = utils.parse_extension(material.url)
        
        # 先检查文件是否真的存在
        if not os.path.exists(material.url):
            logger.error(f"文件不存在: {material.url}")
            continue
        
        # 先尝试获取基本信息
        if ext in const.FILE_TYPE_VIDEOS:
            try:
                # 获取编码和旋转信息
                codec = get_video_codec(material.url)
                rotation = get_video_rotation(material.url)
                
                # 对于HEVC编码可以添加特殊处理
                if "hevc" in codec.lower() or "h265" in codec.lower():
                    logger.info(f"检测到HEVC编码视频: {material.url}")
                    processed_path = preprocess_hevc_video(material.url)
                    if processed_path != material.url:
                        material.url = processed_path
                        logger.info(f"使用预处理后的视频: {material.url}")
                
                # 旋转处理
                if rotation in [90, 270, 180]:
                    logger.info(f"检测到需要旋转的视频: {material.url}, 旋转角度: {rotation}°")
                    fixed_path = fix_video_for_moviepy(material.url, rotation=rotation)
                    if fixed_path != material.url:
                        material.url = fixed_path
                        logger.info(f"使用旋转修正后的视频: {material.url}")
            except Exception as e:
                logger.error(f"处理视频时出错: {material.url}, 错误: {str(e)}")
                # 继续尝试用原始方法
        
        # 下面是原始的处理逻辑
        try:
            if ext in const.FILE_TYPE_VIDEOS:
                # 尝试读取视频，验证可读性
                try:
                    clip = VideoFileClip(material.url)
                    width = clip.size[0]
                    height = clip.size[1]
                    clip.close()
                    
                    if width < 480 or height < 480:
                        logger.warning(f"视频太小，宽: {width}, 高: {height}")
                        continue
                except Exception as e:
                    logger.error(f"无法读取视频: {material.url}, 错误: {str(e)}")
                    continue
            elif ext in const.FILE_TYPE_IMAGES:
                logger.info(f"处理图片: {material.url}")
                try:
                    # 创建一个图片剪辑并设置其持续时间为4秒
                    clip = ImageClip(material.url).with_duration(clip_duration).with_position("center")
                    
                    # 应用缩放效果，使用resize方法
                    # 使用lambda函数使缩放效果随时间动态变化
                    # 缩放效果从原始大小开始，逐渐扩大到120%
                    # t代表当前时间，clip.duration是剪辑的总时长
                    # 注意：1表示100%大小，所以1.2表示120%大小
                    zoom_clip = clip.resize(lambda t: 1 + (clip_duration * 0.03) * (t / clip.duration))
                    
                    # 创建一个包含缩放剪辑的复合视频剪辑
                    final_clip = CompositeVideoClip([zoom_clip])
                    
                    # 输出视频到文件
                    video_file = f"{material.url}.mp4"
                    final_clip.write_videofile(video_file, fps=30, logger=None)
                    final_clip.close()
                    del final_clip
                    material.url = video_file
                    logger.success(f"图片处理完成: {video_file}")
                except Exception as e:
                    logger.error(f"处理图片失败: {material.url}, 错误: {str(e)}")
                    continue
            else:
                logger.warning(f"不支持的文件类型: {material.url}")
                continue
            
        except Exception as e:
            logger.error(f"处理素材失败: {material.url}, 错误: {str(e)}")
            continue
            
    return materials


def force_reencode_video(video_path: str, output_path: str = None, rotation: int = 0) -> str:
    """强制重新编码视频为完全兼容的格式"""
    if output_path is None:
        filename = os.path.basename(video_path)
        dirname = os.path.dirname(video_path)
        output_path = os.path.join(dirname, f"converted_{filename}")
    
    # 检查是否已经存在
    if os.path.exists(output_path):
        return output_path
        
    try:
        # 构建旋转参数
        rotate_filter = ""
        if rotation == 90:
            rotate_filter = "transpose=2"  # 逆时针旋转90度
        elif rotation == 270:
            rotate_filter = "transpose=1"  # 顺时针旋转90度
        elif rotation == 180:
            rotate_filter = "transpose=2,transpose=2"  # 旋转180度
            
        # 直接转码命令，不分离提取和重建步骤
        transcode_cmd = ["ffmpeg", "-y", "-i", video_path]
        
        # 添加旋转滤镜
        vf_filters = []
        if rotate_filter:
            vf_filters.append(rotate_filter)
        
        # 添加色彩空间和格式滤镜
        vf_filters.append("format=yuv420p")
        
        if vf_filters:
            transcode_cmd.extend(["-vf", ",".join(vf_filters)])
            
        # 添加其他参数
        transcode_cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",  # 使用快速预设提高速度
            "-crf", "23",       # 合理的质量
            "-c:a", "aac",      # 音频编码
            "-b:a", "128k",     # 音频比特率
            "-movflags", "+faststart",  # 优化网络播放
            output_path
        ])
        
        # 执行转码
        logger.info(f"重新编码视频: {video_path}")
        logger.info(f"执行命令: {' '.join(transcode_cmd)}")
        
        subprocess.run(transcode_cmd, check=True)
        
        # 验证输出文件
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            # 简单验证视频可读性
            probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", 
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", output_path]
            result = subprocess.run(probe_cmd, capture_output=True, text=True)
            
            if "video" in result.stdout.strip():
                logger.success(f"视频重编码成功: {output_path}")
                return output_path
            else:
                logger.error(f"视频重编码输出不包含视频流")
                return video_path
        else:
            logger.error(f"视频重编码失败或输出为空")
            return video_path
    except Exception as e:
        logger.error(f"视频重编码失败: {str(e)}")
        return video_path


def preprocess_hevc_video(video_path: str) -> str:
    """
    预处理HEVC编码的视频，将其转换为H.264编码
    处理高分辨率视频及旋转信息
    """
    try:
        # 获取元数据
        metadata = VideoMetadataHandler.get_video_metadata(video_path)
        width = metadata.get("width", 0)
        height = metadata.get("height", 0)
        rotation = metadata.get("rotation", 0)
        
        logger.info(f"🎞️ 预处理HEVC视频 | 路径: {video_path}")
        logger.info(f"📊 视频信息: {width}x{height}, 旋转: {rotation}°")
        
        # 如果视频很小或没有旋转，可能不需要处理
        if width <= 1920 and height <= 1920 and rotation == 0:
            return video_path
            
        # 使用fix_video_for_moviepy函数处理
        output_path = fix_video_for_moviepy(video_path, rotation=rotation)
        return output_path
        
    except Exception as e:
        logger.error(f"❌ 预处理HEVC视频出错: {str(e)}")
        return video_path


def fix_video_for_moviepy(video_path: str, output_path: str = None, rotation: int = 0) -> str:
    """
    彻底移除所有元数据和side data，特别是Ambient Viewing Environment
    """
    if output_path is None:
        filename = os.path.basename(video_path)
        dirname = os.path.dirname(video_path)
        output_path = os.path.join(dirname, f"moviepy_compatible_{filename}")
    
    # 检查是否已存在
    if os.path.exists(output_path):
        return output_path
    
    try:
        # 解决方案：完全绕过任何元数据，使用原始视频重新完全编码
        # 使用一个中间临时文件，确保完全移除所有元数据
        temp_raw = os.path.join(os.path.dirname(output_path), f"temp_raw_{os.path.basename(output_path)}")
        
        # 构建旋转滤镜（如果需要）
        rotate_filter = ""
        if rotation == 90:
            rotate_filter = "transpose=2,"  # 逆时针旋转90度
        elif rotation == 270:
            rotate_filter = "transpose=1,"  # 顺时针旋转90度
        elif rotation == 180:
            rotate_filter = "transpose=2,transpose=2,"  # 旋转180度
        
        # 第一步：提取原始视频流，不保留任何元数据
        extract_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-an",  # 不处理音频
            "-vf", f"{rotate_filter}scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",  # 缩放到1080x1920
            "-c:v", "libx264",
            "-crf", "23",  
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-sn", "-dn",  # 不包含字幕和数据流
            "-map_metadata", "-1",  # 移除所有元数据
            "-fflags", "+bitexact",  # 完全按照规范处理
            "-flags:v", "+bitexact",
            "-flags:a", "+bitexact",
            "-x264opts", "no-scenecut",  # 避免场景切换
            temp_raw
        ]
        
        logger.info(f"🛠️ 第一步：提取视频流: {os.path.basename(video_path)}")
        extract_result = subprocess.run(extract_cmd, capture_output=True, text=True)
        
        if extract_result.returncode != 0:
            logger.error(f"❌ 视频流提取失败: {extract_result.stderr}")
            return video_path
            
        # 第二步：使用裸视频重新创建MP4文件，确保完全没有元数据
        final_cmd = [
            "ffmpeg", "-y",
            "-i", temp_raw,
            "-c:v", "copy",  # 直接复制视频流
            "-movflags", "+faststart+frag_keyframe+empty_moov",  # 优化网络播放
            "-fflags", "+bitexact",  # 完全按照规范处理
            "-flags:v", "+bitexact",
            "-map_metadata", "-1",  # 再次移除所有元数据
            output_path
        ]
        
        logger.info(f"🛠️ 第二步：重建无元数据视频文件")
        final_result = subprocess.run(final_cmd, capture_output=True, text=True)
        
        # 清理临时文件
        try:
            if os.path.exists(temp_raw):
                os.remove(temp_raw)
        except:
            pass
            
        if final_result.returncode != 0:
            logger.error(f"❌ 视频重建失败: {final_result.stderr}")
            return video_path
        
        # 验证输出文件
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            # 验证：检查新文件是否存在Ambient Viewing Environment
            check_cmd = ["ffprobe", "-v", "error", "-show_streams", "-of", "json", output_path]
            check_result = subprocess.run(check_cmd, capture_output=True, text=True)
            
            if check_result.returncode == 0:
                try:
                    data = json.loads(check_result.stdout)
                    has_side_data = False
                    for stream in data.get("streams", []):
                        if "side_data_list" in stream:
                            has_side_data = True
                            break
                            
                    if not has_side_data:
                        logger.success(f"✅ 视频修复成功：无side data")
                        
                        # 额外验证：尝试用MoviePy读取
                        try:
                            with VideoFileClip(output_path) as clip:
                                logger.success(f"✅ MoviePy可以读取修复后的视频")
                        except Exception as e:
                            logger.error(f"❌ MoviePy无法读取修复后的视频: {str(e)}")
                            return video_path
                            
                        return output_path
                    else:
                        logger.error(f"❌ 视频仍包含side data")
                except:
                    pass
            
            logger.warning("尝试第三种方法：使用ffmpeg raw格式中转")
            temp_yuv = os.path.join(os.path.dirname(output_path), "temp_yuv.yuv")
            
            # 导出为原始YUV格式
            yuv_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-an",  # 不处理音频
                "-vf", f"{rotate_filter}scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                "-f", "rawvideo",
                "-pix_fmt", "yuv420p",
                temp_yuv
            ]
            
            yuv_result = subprocess.run(yuv_cmd, capture_output=True, text=True)
            
            if yuv_result.returncode == 0 and os.path.exists(temp_yuv):
                # 从YUV重建MP4
                rebuild_cmd = [
                    "ffmpeg", "-y",
                    "-f", "rawvideo",
                    "-pix_fmt", "yuv420p",
                    "-s", "1080x1920",  # 必须指定尺寸
                    "-r", "30",  # 帧率
                    "-i", temp_yuv,
                    "-c:v", "libx264",
                    "-crf", "23",
                    "-preset", "fast",
                    output_path
                ]
                
                rebuild_result = subprocess.run(rebuild_cmd, capture_output=True, text=True)
                
                # 清理临时YUV文件
                try:
                    os.remove(temp_yuv)
                except:
                    pass
                    
                if rebuild_result.returncode == 0:
                    # 测试MoviePy是否可读
                    try:
                        with VideoFileClip(output_path) as clip:
                            logger.success("✅ YUV中转方法成功，MoviePy可读取")
                            return output_path
                    except:
                        pass
            
            # 如果所有方法都失败，直接用我们的ffmpeg函数处理视频
            logger.warning("🔄 所有修复方法失败，直接使用ffmpeg")
            return video_path
        else:
            logger.error(f"❌ 输出视频文件无效")
            return video_path
            
    except Exception as e:
        logger.error(f"❌ 视频修复过程出错: {str(e)}", exc_info=True)
        return video_path

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
            "-map", "0:v:0",
            "-map", "1:a:0",
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
