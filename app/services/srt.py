import re
from pathlib import Path
from fuzzywuzzy import fuzz


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


def match_subtitles_to_scripts(subtitle_entries, scripts):
    """将字幕条目匹配到文案"""
    results = []
    script_idx = 0
    subtitle_idx = 0 

    while script_idx < len(scripts) and subtitle_idx < len(subtitle_entries):
        current_script = scripts[script_idx]
        
        best_match_texts = []
        best_match_similarity = 0
        best_match_end_subtitle_idx = subtitle_idx
        
        current_combined_text = ""
        current_matched_texts = []
        current_total_duration = 0
        current_start_time = subtitle_entries[subtitle_idx]['start']

        temp_subtitle_idx = subtitle_idx
        while temp_subtitle_idx < len(subtitle_entries):
            subtitle_text = subtitle_entries[temp_subtitle_idx]['text']
            
            if not current_combined_text:
                current_combined_text = subtitle_text
            else:
                current_combined_text += subtitle_text

            current_matched_texts.append(subtitle_text)
            current_total_duration += subtitle_entries[temp_subtitle_idx]['duration']

            similarity = fuzz.ratio(current_combined_text, current_script)

            if similarity > best_match_similarity:
                best_match_similarity = similarity
                best_match_texts = list(current_matched_texts)
                best_match_end_subtitle_idx = temp_subtitle_idx + 1

            # 如果相似度足够高，提前结束当前文案的字幕累积
            if similarity >= 90: # 提高阈值，确保高质量匹配
                break
            
            # 如果累积的字幕文本长度远超文案长度，并且相似度很低，则停止累积
            # 避免无意义的累积
            if len(current_combined_text) > len(current_script) * 2 and similarity < 50:
                break
            
            temp_subtitle_idx += 1
        
        # 确定最终匹配结果
        if best_match_similarity >= 75: # 最终确认的阈值，可以比上面的 90 低一些，允许一些不完美匹配
            end_time = subtitle_entries[best_match_end_subtitle_idx - 1]['end']
            results.append({
                'script': current_script,
                'duration_ms': round(current_total_duration / 1000, 3),
                'start_ms': round(current_start_time / 1000, 3),
                'end_ms': round(end_time / 1000, 3),
                'matched_texts': best_match_texts
            })
            subtitle_idx = best_match_end_subtitle_idx # 从最佳匹配的下一个字幕开始
        else:
            # 如果没有找到足够好的匹配，则跳过当前文案，字幕索引只前进一个，尝试匹配下一个文案
            # 这样可以避免一个字幕被多个文案尝试匹配，但可能导致某些字幕被跳过
            # 更复杂的策略可能需要回溯或更智能的字幕索引推进
            subtitle_idx += 1 # 尝试从下一个字幕开始匹配当前文案，或者下一个文案
            continue # 继续下一个文案，字幕索引不变，让下一个文案有机会匹配当前字幕

        script_idx += 1 # 匹配成功，移动到下一个文案

    return results


if __name__ == '__main__':
    # srt_content = Path(r'C:\code\github\MoneyPrinterTurbo\storage\tasks\61d05c47-d99c-49aa-8ec3-cc96d4c009e2\subtitle.srt').read_text(encoding='utf-8')
    srt_content = """
    1
00:00:00,100 --> 00:00:01,062
deepseek说

2
00:00:01,325 --> 00:00:02,663
当你感到焦虑不安

3
00:00:02,875 --> 00:00:04,737
对工作提不起兴致的时候

4
00:00:04,888 --> 00:00:06,450
就去读午夜图书馆

5
00:00:07,013 --> 00:00:09,100
学习的唯一途径就是生活

6
00:00:09,662 --> 00:00:10,887
在生与死之间

7
00:00:11,175 --> 00:00:12,238
有一座图书馆

8
00:00:12,800 --> 00:00:14,062
在这座图书馆里

9
00:00:14,275 --> 00:00:15,500
书架绵延不绝

10
00:00:16,062 --> 00:00:20,938
每一本书都提供了一次尝试另一种你可能活过的生活的机会

11
00:00:21,500 --> 00:00:24,000
去看看如果你做了其他的选择

12
00:00:24,288 --> 00:00:25,625
事情会变成怎样

13
00:00:25,938 --> 00:00:27,950
如果你有机会消除你的遗憾

14
00:00:28,238 --> 00:00:29,775
你会做些什么不同的事

15
00:00:30,337 --> 00:00:31,600
你不必理解生活

16
00:00:32,163 --> 00:00:33,375
你只需要去过它


    """
    subtitle_entries = calculate_durations(srt_content)
    print(subtitle_entries)
    matched_results = match_subtitles_to_scripts(subtitle_entries, ["deepseek说，当你感到焦虑不安，对工作提不起兴致的时候，就去读《午夜图书馆》。",
                       "学习的唯一途径就是生活。",
                       "在生与死之间，有一座图书馆。在这座图书馆里，书架绵延不绝。每一本书都提供了一次尝试另一种你可能活过的生活的机会。去看看如果你做了其他的选择，事情会变成怎样……如果你有机会消除你的遗憾，你会做些什么不同的事？",
                       "你不必理解生活。你只需要去过它。"])
    for result in matched_results:
        print(result)
