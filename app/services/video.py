import glob
import os
import random
from typing import List

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
import json
import subprocess
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
    """获取视频旋转元数据，支持displaymatrix和rotate标签"""
    try:
        # 获取常规rotate标签
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream_tags=rotate",
            "-of", "csv=p=0",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        rotation_str = result.stdout.strip()
        
        # 如果没有rotate标签，尝试获取displaymatrix信息
        if not rotation_str:
            cmd = [
                "ffprobe",
                "-v", "error", 
                "-select_streams", "v:0",
                "-show_entries", "side_data=rotation",
                "-of", "json",
                video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            try:
                data = json.loads(result.stdout)
                for stream in data.get("streams", []):
                    for side_data in stream.get("side_data_list", []):
                        if side_data.get("side_data_type") == "Display Matrix":
                            rotation_str = str(side_data.get("rotation", 0))
                            break
                
                # 还是没有找到，尝试查找displaymatrix字段
                if not rotation_str:
                    cmd = [
                        "ffprobe",
                        "-v", "error", 
                        "-select_streams", "v:0",
                        "-show_entries", "side_data",
                        "-of", "json",
                        video_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if "rotation of -90" in result.stdout:
                        return 90  # -90度逆时针等于90度顺时针
                    elif "rotation of 90" in result.stdout:
                        return 270  # 90度顺时针在旋转处理中等同于270度角
                    elif "rotation of 180" in result.stdout or "rotation of -180" in result.stdout:
                        return 180
            except:
                pass

        # 把字符串转换为整数
        if rotation_str:
            # 处理负角度（如 -90）
            if rotation_str.startswith('-'):
                # 负角度需要转换为正角度
                angle = int(rotation_str)
                if angle == -90:
                    return 90
                elif angle == -270:
                    return 270
                else:
                    return abs(angle) % 360
            else:
                return int(float(rotation_str)) % 360
            
        return 0
    except Exception as e:
        logger.warning(f"获取视频旋转信息失败: {str(e)}")
        return 0

def get_video_codec(video_path: str) -> str:
    """获取视频编码格式"""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "csv=p=0",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout.strip()
    except:
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
):
    # 在函数开始处导入
    # from moviepy.video.fx.rotate import rotate
    # from moviepy.video.fx.all import rotate
    
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
    processed_paths = []  # 存储处理过的临时文件
    
    for video_path in video_paths:
        try:
            # 添加：获取视频元数据检查旋转信息
            rotation = get_video_rotation(video_path)
                
            # 添加：检查文件路径判断预期方向
            expected_portrait = "竖屏" in video_path

            # 添加：使用ffprobe检查编码格式（H.264/H.265）
            codec = get_video_codec(video_path)
            logger.info(f"加载视频: {video_path}, 旋转角度: {rotation}°, 编码: {codec}")
            
            # 对于HEVC编码视频进行预处理
            if "hevc" in codec.lower() or "h265" in codec.lower():
                processed_path = preprocess_hevc_video(video_path)
                if processed_path != video_path:
                    processed_paths.append(processed_path)
                    video_path = processed_path
                    logger.info(f"使用预处理后的视频: {video_path}")
                
            # 读取视频片段
            clip = VideoFileClip(video_path).without_audio()
            
            # 处理可能仍需要的旋转
            if rotation in [90, 270]:
                logger.info(f"视频需要旋转 {rotation}°: {video_path}")
                if rotation == 90:
                    clip = clip.rotate(90)  # 使用MoviePy的rotate方法
                elif rotation == 270:
                    clip = clip.rotate(-90)  # 使用MoviePy的rotate方法
            
            # 添加：如果路径指示竖屏但视频是横屏，强制旋转
            elif expected_portrait and clip.w > clip.h:
                logger.info(f"根据路径强制旋转视频: {video_path}")
                clip = clip.rotate(90)  # 使用MoviePy的rotate方法
        
        except Exception as e:
            logger.error(f"处理视频失败: {video_path}, 错误: {str(e)}")
            continue
        clip_duration = clip.duration
        start_time = 0

        while start_time < clip_duration:
            end_time = min(start_time + max_clip_duration, clip_duration)
            split_clip = clip.subclipped(start_time, end_time)
            raw_clips.append(split_clip)
            # logger.info(f"splitting from {start_time:.2f} to {end_time:.2f}, clip duration {clip_duration:.2f}, split_clip duration {split_clip.duration:.2f}")
            start_time = end_time
            if video_concat_mode.value == VideoConcatMode.sequential.value:
                break

    # random video_paths order
    if video_concat_mode.value == VideoConcatMode.random.value:
        random.shuffle(raw_clips)

    # Add downloaded clips over and over until the duration of the audio (max_duration) has been reached
    while video_duration < audio_duration:
        for clip in raw_clips:
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
            if video_transition_mode.value == VideoTransitionMode.none.value:
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
    clips = [CompositeVideoClip([clip]) for clip in clips]
    video_clip = concatenate_videoclips(clips)
    video_clip = video_clip.with_fps(30)
    logger.info("writing")
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
    logger.success("completed")

    # 函数结束前清理临时文件
    try:
        for temp_path in processed_paths:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                logger.info(f"已删除临时处理文件: {temp_path}")
    except Exception as e:
        logger.warning(f"清理临时文件失败: {str(e)}")

    return combined_video_path


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


def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
    for material in materials:
        if not material.url:
            continue

        ext = utils.parse_extension(material.url)
        
        # 先检查文件是否真的存在
        if not os.path.exists(material.url):
            logger.error(f"文件不存在: {material.url}")
            continue
            
        # 先确认文件类型，避免错误尝试
        if ext in const.FILE_TYPE_IMAGES:
            # 图片处理逻辑
            logger.info(f"处理图片: {material.url}")
            try:
                clip = ImageClip(material.url).with_duration(clip_duration).with_position("center")
                # 创建缩放效果
                zoom_clip = clip.resize(lambda t: 1 + (clip_duration * 0.03) * (t / clip.duration))
                final_clip = CompositeVideoClip([zoom_clip])
                video_file = f"{material.url}.mp4"
                final_clip.write_videofile(video_file, fps=30, logger=None)
                material.url = video_file
                logger.success(f"图片处理完成: {video_file}")
            except Exception as e:
                logger.error(f"处理图片失败: {material.url}, 错误: {str(e)}")
                continue
                
        elif ext in const.FILE_TYPE_VIDEOS:
            # 视频处理逻辑
            logger.info(f"处理视频: {material.url}")
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
                
            except Exception as e:
                logger.error(f"处理视频失败: {material.url}, 错误类型: {type(e).__name__}, 错误信息: {str(e)}")
                continue
        else:
            logger.warning(f"不支持的文件类型: {material.url}")
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
        # 创建临时目录
        temp_dir = os.path.join(os.path.dirname(output_path), "temp_video")
        os.makedirs(temp_dir, exist_ok=True)
        temp_output = os.path.join(temp_dir, "temp_output.mp4")
        
        # 构建旋转参数
        rotate_filter = ""
        if rotation == 90:
            rotate_filter = "transpose=2"  # 逆时针旋转90度
        elif rotation == 270:
            rotate_filter = "transpose=1"  # 顺时针旋转90度
        elif rotation == 180:
            rotate_filter = "transpose=2,transpose=2"  # 旋转180度
            
        # 尝试简单的图像提取和重建方法，直接从视频中提取帧并重建
        extract_frames_cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-qscale:v", "1",  # 高质量 JPG
            "-r", "30", # 提取帧率
        ]
        
        # 添加旋转滤镜
        if rotate_filter:
            extract_frames_cmd.extend(["-vf", rotate_filter])
            
        # 输出到临时目录
        frames_pattern = os.path.join(temp_dir, "frame_%04d.jpg")
        extract_frames_cmd.append(frames_pattern)
        
        # 执行提取帧命令
        logger.info(f"从视频中提取帧: {video_path}")
        subprocess.run(extract_frames_cmd, capture_output=True, check=True)
        
        # 重建视频
        rebuild_cmd = [
            "ffmpeg", "-y",
            "-framerate", "30",
            "-i", frames_pattern,
            "-i", video_path,  # 用于获取音频
            "-map", "0:v",  # 使用第一个输入的视频流
            "-map", "1:a",  # 使用第二个输入的音频流
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",  # 8位色深
            "-c:a", "aac",
            "-shortest",  # 确保音视频长度匹配
            "-vf", "format=yuv420p",  # 强制色彩格式
            output_path
        ]
        
        # 执行重建命令
        logger.info(f"重新构建视频: {output_path}")
        subprocess.run(rebuild_cmd, capture_output=True, check=True)
        
        # 清理临时文件
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except:
            logger.warning(f"清理临时文件失败")
            
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.success(f"视频重编码成功: {output_path}")
            return output_path
        else:
            logger.error(f"视频重编码失败")
            return video_path
    except Exception as e:
        logger.error(f"视频重编码失败: {str(e)}")
        return video_path


def preprocess_hevc_video(video_path: str) -> str:
    """预处理HEVC编码的视频，转换为更兼容的格式"""
    try:
        # 获取视频旋转信息
        rotation = get_video_rotation(video_path)
        logger.info(f"检测到视频旋转角度: {rotation}°")
        
        # 使用简化的编码方案，完全兼容MoviePy
        return force_reencode_video(video_path, rotation=rotation)
    except Exception as e:
        logger.error(f"预处理HEVC视频出错: {str(e)}")
        return video_path


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
