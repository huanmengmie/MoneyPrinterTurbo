import gc
import glob
import os
import random
import subprocess
import time
import traceback
from typing import List

from PIL import ImageFont
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

from app.models import const
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode, TaskVideo2Request,
)
from app.utils import utils


class SubClippedVideoClip:
    def __init__(self, file_path, start_time=None, end_time=None, width=None, height=None, duration=None):
        self.file_path = file_path
        self.start_time = start_time
        self.end_time = end_time
        self.width = width
        self.height = height
        if duration is None:
            self.duration = end_time - start_time
        else:
            self.duration = duration

    def __str__(self):
        return f"SubClippedVideoClip(file_path={self.file_path}, start_time={self.start_time}, end_time={self.end_time}, duration={self.duration}, width={self.width}, height={self.height})"


audio_codec = "aac"
video_codec = "libx264"
fps = 30
DEFAULT_TRANSITION_DURATION = 0.2  # seconds, default transition duration


def close_clip(clip):
    if clip is None:
        return

    try:
        # close main resources
        if hasattr(clip, 'reader') and clip.reader is not None:
            clip.reader.close()

        # close audio resources
        if hasattr(clip.audio, 'reader') and clip.audio.reader is not None:
            if hasattr(clip.audio, 'reader') and clip.audio.reader is not None:
                clip.audio.reader.close()
            del clip.audio

        # close mask resources
        if hasattr(clip, 'mask') and clip.mask is not None:
            if hasattr(clip.mask, 'reader') and clip.mask.reader is not None:
                clip.mask.reader.close()
            del clip.mask

        # handle child clips in composite clips
        if hasattr(clip, 'clips') and clip.clips:
            for child_clip in clip.clips:
                if child_clip is not clip:  # avoid possible circular references
                    close_clip(child_clip)

        # clear clip list
        if hasattr(clip, 'clips'):
            clip.clips = []

    except Exception as e:
        logger.error(f"failed to close clip: {str(e)}")

    del clip


def delete_files(files: List[str] | str):
    if isinstance(files, str):
        files = files

    for file in files:
        try:
            os.remove(file)
        except:
            pass


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


def get_video_duration_ffprobe(file_path: str) -> float | None:
    """
    使用 ffprobe 获取视频文件的精确持续时间。
    """
    command = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        file_path
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get video duration with ffprobe for {file_path}: {e}")
        return None


@utils.timeit
def combine_videos(
        combined_video_path: str,
        video_paths: List[str],
        audio_file: str,
        video_aspect: VideoAspect = VideoAspect.portrait,
        video_concat_mode: VideoConcatMode = VideoConcatMode.sequential,
        video_transition_mode: VideoTransitionMode = VideoTransitionMode.fade_in,
) -> str:
    audio_clip = AudioFileClip(audio_file)
    audio_duration = audio_clip.duration
    logger.info(f"audio duration: {audio_duration} seconds")
    output_dir = os.path.dirname(combined_video_path)

    aspect = VideoAspect(video_aspect)
    video_width, video_height = aspect.to_resolution()

    processed_clips = []
    subclipped_items = []
    video_duration = 0
    for video_path in video_paths:
        clip = VideoFileClip(video_path)
        clip_duration = clip.duration
        clip_w, clip_h = clip.size
        close_clip(clip)

        start_time = 0
        subclipped_items.append(
            SubClippedVideoClip(file_path=video_path, start_time=start_time, end_time=clip_duration, width=clip_w,
                                height=clip_h))

    # random subclipped_items order
    if video_concat_mode.value == VideoConcatMode.random.value:
        random.shuffle(subclipped_items)

    logger.debug(f"total subclipped items: {len(subclipped_items)}")

    for i, subclipped_item in enumerate(subclipped_items):
        logger.debug(
            f"processing clip {i + 1}: {subclipped_item.width}x{subclipped_item.height}, current duration: {video_duration:.2f}s, remaining: {audio_duration - video_duration:.2f}s")

        try:
            clip = VideoFileClip(subclipped_item.file_path).subclipped(subclipped_item.start_time,
                                                                       subclipped_item.end_time)
            clip_duration = clip.duration
            # Not all videos are same size, so we need to resize them
            clip_w, clip_h = clip.size
            if clip_w != video_width or clip_h != video_height:
                clip_ratio = clip.w / clip.h
                video_ratio = video_width / video_height
                logger.debug(
                    f"resizing clip, source: {clip_w}x{clip_h}, ratio: {clip_ratio:.2f}, target: {video_width}x{video_height}, ratio: {video_ratio:.2f}")

                if clip_ratio == video_ratio:
                    clip = clip.resized(new_size=(video_width, video_height))
                else:
                    if clip_ratio > video_ratio:
                        scale_factor = video_width / clip_w
                    else:
                        scale_factor = video_height / clip_h

                    new_width = int(clip_w * scale_factor)
                    new_height = int(clip_h * scale_factor)

                    background = ColorClip(size=(video_width, video_height), color=(0, 0, 0)).with_duration(
                        clip_duration)
                    clip_resized = clip.resized(new_size=(new_width, new_height)).with_position("center")
                    clip = CompositeVideoClip([background, clip_resized])

            # wirte clip to temp file
            clip_file = f"{output_dir}/temp-clip-{i + 1}.mp4"
            clip.write_videofile(clip_file, logger=None, fps=fps, codec=video_codec)
            time.sleep(0.1)  # Add a small delay to ensure file is fully written
            close_clip(clip)

            # Get actual duration of the written clip file using ffprobe
            actual_clip_duration = get_video_duration_ffprobe(clip_file)
            if actual_clip_duration is None:
                logger.warning(f"Could not get actual duration for {clip_file}, using estimated duration.")
                actual_clip_duration = clip_duration  # Fallback to moviepy's duration

            processed_clips.append(
                SubClippedVideoClip(file_path=clip_file, duration=actual_clip_duration, width=clip_w, height=clip_h))
            video_duration += actual_clip_duration

        except Exception as e:
            logger.error(f"failed to process clip: {str(e)}")

    logger.debug(f"Processed clips details:")
    for idx, clip_item in enumerate(processed_clips):
        logger.debug(f"  Clip {idx}: {clip_item.file_path}, Duration: {clip_item.duration:.2f}s")
    logger.debug(f"Total video duration from processed clips: {video_duration:.2f}s")

    # Use FFmpeg xfade filter to merge video clips with transitions
    logger.info("starting FFmpeg xfade process")
    if not processed_clips:
        logger.warning("no clips available for merging")
        return combined_video_path

    # Prepare inputs for FFmpeg
    ffmpeg_inputs = []
    for clip_item in processed_clips:
        ffmpeg_inputs.extend(['-i', clip_item.file_path])

    # Add audio file as an input
    audio_input_index = len(processed_clips)
    ffmpeg_inputs.extend(['-i', audio_file])

    # Build complex filtergraph for xfade transitions
    filter_graph = []
    transition_duration = DEFAULT_TRANSITION_DURATION  # seconds, as per user's request for 0.2s fade

    # Map VideoTransitionMode to FFmpeg xfade types
    xfade_types = {
        VideoTransitionMode.none: "concat",  # Use concat filter if no transition
        VideoTransitionMode.fade_in: "fade",
        VideoTransitionMode.fade_out: "fade",  # fade_out is handled by fade type in xfade
        VideoTransitionMode.slide_in: ["slideup", "slidedown", "slideright", "slideleft"],  # Will pick randomly
        VideoTransitionMode.slide_out: ["slideup", "slidedown", "slideright", "slideleft"],  # Will pick randomly
        VideoTransitionMode.shuffle: ["fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideup", "slidedown",
                                      "slideright", "slideleft", "dissolve"],  # More options for shuffle
    }

    # Build xfade chain
    # The first input stream is [0:v], the second is [1:v], etc.
    # The output of each xfade is named [v_out_i]
    # The next xfade takes [v_out_i] and [i+1:v] as inputs

    # If there's only one clip, no xfade is needed, just map it directly
    if len(processed_clips) == 1:
        final_video_stream = "[0:v]"
    else:
        # Start with the first two clips
        current_output_stream = f"[0:v][1:v]"
        current_cumulative_duration = processed_clips[0].duration

        # Determine xfade type for the first transition
        xfade_type = "fade"  # Default to fade
        if video_transition_mode == VideoTransitionMode.none:
            xfade_type = "concat"
        elif video_transition_mode == VideoTransitionMode.fade_in or video_transition_mode == VideoTransitionMode.fade_out:
            xfade_type = "fade"
        elif video_transition_mode == VideoTransitionMode.slide_in or video_transition_mode == VideoTransitionMode.slide_out:
            xfade_type = random.choice(["slideup", "slidedown", "slideright", "slideleft"])
        elif video_transition_mode == VideoTransitionMode.shuffle:
            xfade_type = random.choice(xfade_types[VideoTransitionMode.shuffle])

        if xfade_type == "concat":
            filter_graph.append(f"{current_output_stream}concat=n=2:v=1:a=0[v_out_0]")
        else:
            # Offset for xfade should be (duration of previous video) - (transition duration)
            offset = current_cumulative_duration - transition_duration
            filter_graph.append(
                f"{current_output_stream}xfade=transition={xfade_type}:duration={transition_duration}:offset={offset}[v_out_0]")

        current_cumulative_duration += processed_clips[
                                           1].duration - transition_duration  # Subtract transition duration from next clip's effective duration

        # Chain subsequent clips
        for i in range(2, len(processed_clips)):
            prev_output_stream = f"[v_out_{i - 2}]"
            next_input_stream = f"[{i}:v]"

            # Determine xfade type for current transition
            xfade_type = "fade"  # Default to fade
            if video_transition_mode == VideoTransitionMode.none:
                xfade_type = "concat"
            elif video_transition_mode == VideoTransitionMode.fade_in or video_transition_mode == VideoTransitionMode.fade_out:
                xfade_type = "fade"
            elif video_transition_mode == VideoTransitionMode.slide_in or video_transition_mode == VideoTransitionMode.slide_out:
                xfade_type = random.choice(["slideup", "slidedown", "slideright", "slideleft"])
            elif video_transition_mode == VideoTransitionMode.shuffle:
                xfade_type = random.choice(xfade_types[VideoTransitionMode.shuffle])

            if xfade_type == "concat":
                filter_graph.append(f"{prev_output_stream}{next_input_stream}concat=n=2:v=1:a=0[v_out_{i - 1}]")
            else:
                # Offset for xfade should be (duration of previous combined video) - (transition duration)
                offset = current_cumulative_duration - transition_duration
                filter_graph.append(
                    f"{prev_output_stream}{next_input_stream}xfade=transition={xfade_type}:duration={transition_duration}:offset={offset}[v_out_{i - 1}]")

            current_cumulative_duration += processed_clips[
                                               i].duration - transition_duration  # Subtract transition duration from next clip's effective duration

        final_video_stream = f"[v_out_{len(processed_clips) - 2}]"  # The last output stream

    # FFmpeg command
    command = [
        'ffmpeg',
        '-y',  # Overwrite output files without asking
    ]
    command.extend(ffmpeg_inputs)  # Add all input files
    command.extend([
        '-filter_complex', ';'.join(filter_graph) if filter_graph else '',
        # Add the complex filtergraph, handle empty case
        '-map', f"{final_video_stream}",  # Map the final video stream
        '-map', f"{audio_input_index}:a",  # Map audio from the audio_file
        '-pix_fmt', 'yuv420p',  # Add pixel format for wider compatibility
        '-c:v', video_codec,
        '-c:a', audio_codec,
        # '-shortest', # Temporarily remove -shortest to debug video length
        combined_video_path
    ])

    # Handle case where filter_complex is empty (e.g., only one video clip)
    if not filter_graph:
        # Reconstruct command for single clip case
        command = [
            'ffmpeg',
            '-y',
            '-i', processed_clips[0].file_path,  # First video input
            '-i', audio_file,  # Audio input
            '-map', '0:v',  # Map video from first input
            '-map', '1:a',  # Map audio from second input (audio_file)
            '-pix_fmt', 'yuv420p',  # Add pixel format for wider compatibility
            '-c:v', video_codec,
            '-c:a', audio_codec,
            # '-shortest', # Temporarily remove -shortest to debug video length
            combined_video_path
        ]

    logger.info(f"FFmpeg command: {' '.join(command)}")

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        logger.success("FFmpeg xfade process completed successfully")
    except FileNotFoundError:
        logger.error("Error: 'ffmpeg' not found. Please ensure FFmpeg is installed and in your PATH.")
        return combined_video_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Error during FFmpeg execution: {e.stderr}")
        return combined_video_path
    finally:
        # Clean up temporary files
        clip_files = [clip.file_path for clip in processed_clips]
        delete_files(clip_files)  # Only delete processed clips, not transition clips if they were generated

    logger.info("video combining completed")
    gc.collect()  # Perform garbage collection after all operations are done
    return combined_video_path


@utils.timeit
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
    return result, height


def create_zoom_video_rock_solid_smooth(image_path: str, output_path: str, duration: float, fps: int = 30,
                                        zoom_factor: float = 1.1, target_size: tuple = (1080, 1920)):
    """
    通过预先放大分辨率，创建更平滑的缩放视频。
    """
    total_frames = int(duration * fps)
    out_w, out_h = target_size

    # 预先将图片放大到 8000 像素宽，以便缩放时更平滑
    pre_scale_width = 8000

    zoom_expr = f"1+(on/{total_frames})*({zoom_factor}-1)"
    x_expr = f"trunc((iw-iw/({zoom_expr}))/2)"
    y_expr = f"trunc((ih-ih/({zoom_expr}))/2)"

    filter_string = (
        f"scale={pre_scale_width}:-1,"  # 预先放大分辨率
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"d={total_frames}:"
        f"s={out_w}x{out_h}"
    )

    command = [
        'ffmpeg',
        '-y',
        '-loop', '1',
        '-i', image_path,
        '-vf', filter_string,
        '-c:v', 'libx264',
        '-t', str(duration),
        '-pix_fmt', 'yuv420p',
        '-r', str(fps),
        '-preset', 'veryfast',
        output_path
    ]

    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        return True
    except FileNotFoundError:
        print(f"Error: 'ffmpeg' not found.")
        return False
    except subprocess.CalledProcessError as e:
        print("Error during FFmpeg execution:")
        print("Command:", ' '.join(e.cmd))
        print("Return code:", e.returncode)
        print("Stderr:", e.stderr)
        return False


@utils.timeit
def preprocess_video(materials: List[MaterialInfo], target_size=(1080, 1920), zoom_factor=1.1):
    for material in materials:
        if not material.url:
            continue

        ext = utils.parse_extension(material.url)
        try:
            clip = VideoFileClip(material.url)
        except Exception:
            clip = ImageClip(material.url)

        width = clip.size[0]
        height = clip.size[1]
        if width < 480 or height < 480:
            logger.warning(f"low resolution material: {width}x{height}, minimum 480x480 required")
            continue
        try:
            if ext in const.FILE_TYPE_IMAGES:
                logger.info(f"processing image: {material.url}, {material.duration}")
                video_file = f"{material.url}.mp4"
                # Increase duration for image-generated videos to accommodate transitions
                adjusted_duration = material.duration + DEFAULT_TRANSITION_DURATION
                success = create_zoom_video_rock_solid_smooth(  # 调用优化后的函数
                    image_path=material.url,
                    output_path=video_file,
                    duration=adjusted_duration,
                    target_size=target_size,
                    zoom_factor=zoom_factor,
                )

                if success:
                    material.url = video_file
                    logger.success(f"Image processed via FFmpeg: {video_file}")
                else:
                    logger.error(f"Failed to process image with FFmpeg: {material.url}")
                logger.success(f"image processed: {video_file}")
            elif ext in const.FILE_TYPE_VIDEOS:
                if clip.duration != material.duration:
                    if clip.duration < material.duration:
                        # 循环补足时长
                        loops = int(material.duration / clip.duration) + 1
                        final_clip = concatenate_videoclips([clip] * loops).subclip(0, material.duration)
                    else:
                        # 截取中间部分
                        start_time = (clip.duration - material.duration) / 2
                        final_clip = clip.subclip(start_time, start_time + material.duration)
                    # 输出处理后的视频
                    video_file = f"{material.url}_adjusted.mp4"
                    final_clip.write_videofile(video_file, fps=30, logger=None)
                    close_clip(clip)
                    material.url = video_file
                    logger.success(f"video duration adjusted: {material.duration}s")
        except Exception as e:
            logger.error(f"failed to process material: {str(e)}")
            traceback.print_exc()
    return materials


@utils.timeit
def generate_video(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    aspect = VideoAspect(params.video_aspect)
    video_width, video_height = aspect.to_resolution()

    logger.info(f"generating video: {video_width} x {video_height}")
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

        logger.info(f"  ⑤ font: {font_path}")

    def create_text_clip(subtitle_item):
        params.font_size = int(params.font_size)
        params.stroke_width = int(params.stroke_width)
        phrase = subtitle_item[1]
        max_width = video_width * 0.9
        wrapped_txt, txt_height = wrap_text(
            phrase, max_width=max_width, font=font_path, fontsize=params.font_size
        )
        interline = int(params.font_size * 0.25)
        size=(int(max_width), int(txt_height + params.font_size * 0.25 + (interline * (wrapped_txt.count("\n") + 1))))

        _clip = TextClip(
            text=wrapped_txt,
            font=font_path,
            font_size=params.font_size,
            color=params.text_fore_color,
            bg_color=params.text_background_color,
            stroke_color=params.stroke_color,
            stroke_width=params.stroke_width,
            # interline=interline,
            # size=size,
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

    video_clip = VideoFileClip(video_path).without_audio()
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
        audio_codec=audio_codec,
        temp_audiofile_path=output_dir,
        threads=params.n_threads or 8,
        logger='bar',  # 显示进度条
        fps=fps,
    )
    video_clip.close()
    del video_clip

if __name__ == '__main__':
    preprocess_video([MaterialInfo(url=f'C:/code/github/MoneyPrinterTurbo/test/video/{i}.png', duration=5) for i in (1, )])
    # combine_videos(combined_video_path=r'C:\code\github\MoneyPrinterTurbo\test\combine\combined.mp4',
    #                video_paths=[f'C:/code/github/MoneyPrinterTurbo/test/resources/{i}.png.mp4' for i in range(8)],
    #                audio_file=r'C:\code\github\MoneyPrinterTurbo\test\combine\all_audio.mp3')

    # params = TaskVideo2Request(
    #     video_subject='测试',
    #     video_script=["早睡早起精神好，子午小憩不可少。",
    #                   "三餐规律营养全，五谷蔬果多尝鲜。",
    #                   "常饮热茶驱寒气，蜂蜜枸杞润肺脾。",
    #                   "每日步行千步走，气血通畅病不有。",
    #                   "梳头百遍头不晕，耳常按摩听力稳。",
    #                   "冷水洗脸身耐寒，热水泡脚睡眠安。",
    #                   "情绪稳定少烦恼，笑口常开疾病跑。",
    #                   "日光之下常晒晒，阴阳调和身自在。"],
    #     video_materials=[MaterialInfo(url=f'C:/code/github/MoneyPrinterTurbo/test/resources/{i}.png') for i in
    #                      range(8)],
    #     voice_name="zh-CN-XiaoyiNeural-Female",
    #     voice_rate=1.0,
    #     video_source="local",
    # )
    #
    # generate_video(video_path=r'C:\code\github\MoneyPrinterTurbo\test\combine\combined.mp4',
    #                audio_path=r'C:\code\github\MoneyPrinterTurbo\test\combine\all_audio.mp3',
    #                subtitle_path=r'C:\code\github\MoneyPrinterTurbo\test\combine\subtitle.srt',
    #                output_file=r'C:\code\github\MoneyPrinterTurbo\test\combine\final.mp4',
    #                params=params)
    pass
