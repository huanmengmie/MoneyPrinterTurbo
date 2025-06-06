import re
from difflib import SequenceMatcher


def parse_time(time_str):
    """将时间字符串转换为毫秒数"""
    hh, mm, ss_ms = time_str.split(':')
    ss, ms = ss_ms.split(',')
    return int(hh) * 3600 * 1000 + int(mm) * 60 * 1000 + int(ss) * 1000 + int(ms)


def calculate_durations(srt_content):
    """提取字幕文本和对应时间"""
    entries = []
    lines = [line.strip() for line in srt_content.split('\n') if line.strip()]

    i = 0
    while i < len(lines):
        if lines[i].isdigit():  # 字幕序号行
            time_line = lines[i + 1]
            text_line = lines[i + 2] if i + 2 < len(lines) else ""

            start_end = time_line.split(' --> ')
            start = parse_time(start_end[0].strip())
            end = parse_time(start_end[1].strip())

            entries.append({
                'text': text_line,
                'start': start,
                'end': end,
                'duration': end - start
            })
            i += 3
        else:
            i += 1

    return entries


def text_similarity(a, b):
    """计算文本相似度"""
    return SequenceMatcher(None, a, b).ratio()


def match_subtitles_to_scripts(subtitle_entries, scripts):
    """将字幕条目匹配到文案"""
    results = []
    script_idx = 0

    # 预处理：去除文案中的标点以便更好匹配
    clean_scripts = [re.sub(r'[，。、；！？]', '', script) for script in scripts]

    i = 0
    while i < len(subtitle_entries) and script_idx < len(clean_scripts):
        current_script = clean_scripts[script_idx]
        matched_texts = []
        total_duration = 0
        start_time = subtitle_entries[i]['start']

        # 尝试匹配连续的字幕直到匹配到完整文案
        while i < len(subtitle_entries):
            subtitle_text = subtitle_entries[i]['text']
            matched_texts.append(subtitle_text)
            total_duration += subtitle_entries[i]['duration']

            # 检查当前匹配的文本是否已覆盖整个文案
            combined_text = ''.join(matched_texts)
            similarity = text_similarity(combined_text, current_script)

            if similarity > 0.7:  # 相似度阈值
                end_time = subtitle_entries[i]['end']
                results.append({
                    'script': scripts[script_idx],
                    'duration_ms': round(total_duration / 1000, 3),
                    'start_ms': round(start_time / 1000, 3),
                    'end_ms': round(end_time / 1000, 3),
                    'matched_texts': matched_texts
                })
                script_idx += 1
                i += 1
                break
            elif similarity < 0.3 and len(matched_texts) > 1:
                # 如果相似度太低且已经尝试匹配多个字幕，可能匹配错误
                i -= len(matched_texts) - 1  # 回退
                script_idx += 1  # 跳过当前文案
                break
            else:
                i += 1

    return results
