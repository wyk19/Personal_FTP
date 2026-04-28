import os
import requests
import re
import json
import time
import datetime
import string


def generate_random_did() -> str:
    """生成随机设备指纹"""
    chars = string.ascii_lowercase + string.digits
    import random
    random_hash = ''.join(random.choices(chars, k=32))
    return f"web_{random_hash}"


def resolve_single_playback(video_id: str) -> str:
    """解析单条回放视频的 m3u8 地址"""
    url = f"https://live.kuaishou.com/playback/{video_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1",
        "Cookie": f"did={generate_random_did()};"
    }

    try:
        sess = requests.Session()
        sess.trust_env = False
        requests.packages.urllib3.disable_warnings()

        resp = sess.get(url, headers=headers, verify=False, timeout=10)
        html = resp.text

        target_html = html.split('"playbackInfo"')[-1] if '"playbackInfo"' in html else html

        json_url_match = re.search(r'"url":"(https?://[^"]+?\.m3u8[^"]*?)"', target_html)
        if json_url_match: return json_url_match.group(1).replace(r'\u002F', '/').replace(r'\/', '/')

        json_hls_match = re.search(r'"hlsUrl":"(https?://[^"]+?\.m3u8[^"]*?)"', target_html)
        if json_hls_match: return json_hls_match.group(1).replace(r'\u002F', '/').replace(r'\/', '/')

        m_all = re.findall(r'(https?://[^\s"\'\\]+?\.m3u8[^\s"\'\\]*)', target_html)
        if m_all: return m_all[0].replace(r'\u002F', '/').replace(r'\/', '/')

    except Exception as e:
        print(f"[-] 单视频 {video_id} 解析异常: {e}")

    return None


def get_all_playbacks(uid: str, user_cookie: str = ""):
    """靠游标自然结束，获取主播的【所有】回放列表"""
    print(f"[*] 开始获取用户 {uid} 的全部回放列表...")

    api_url = "https://live.kuaishou.com/live_api/playback/list"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://live.kuaishou.com/profile/{uid}",
        "Content-Type": "application/json",
        "Cookie": user_cookie if user_cookie else f"did={generate_random_did()};"
    }

    params = {
        "principalId": uid,
        "count": 12,
        "cursor": ""
    }

    sess = requests.Session()
    sess.trust_env = False
    requests.packages.urllib3.disable_warnings()

    results = []
    page_count = 1

    while True:  # 直接使用死循环，依靠接口返回的游标决定何时退出
        try:
            print(f"[*] 请求第 {page_count} 页 (Cursor: {params['cursor']})...")
            resp = sess.get(api_url, headers=headers, params=params, verify=False, timeout=15)
            data = resp.json()
            inner = data.get('data', {})
            v_list = inner.get('list', [])

            if not v_list:
                if inner.get('result') != 1:
                    print(f"[-] API 返回异常 (Code {inner.get('result')})，可能触发风控，请尝试填入真实 Cookie。")
                else:
                    print("[*] 列表数据为空，加载完毕。")
                break

            for obj in v_list:
                vid = obj.get('id')
                ts_ms = obj.get('createTime', 0)
                try:
                    # 强制指定为 UTC+8 (北京时间)
                    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
                    dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=tz_bj).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    dt = "未知时间"

                print(f"  -> 解析中 ({len(results) + 1}): {dt}")
                real_m3u8 = resolve_single_playback(vid)

                if real_m3u8:
                    results.append({"vid": vid, "time": dt, "url": real_m3u8})
                else:
                    print(f"     [-] 失败: 未能解析出源地址。")

                time.sleep(0.5)

                # 原汁原味的终止判断条件：没有游标了就撤
            pcursor = inner.get('pcursor')
            if not pcursor or pcursor == "no_more":
                print("[*] 服务器返回 no_more，已到达历史记录最末尾。")
                break

            params['cursor'] = pcursor
            page_count += 1
            time.sleep(1)

        except Exception as e:
            print(f"[-] 列表请求异常: {e}")
            break

    print(f"\n[*] 任务结束，共成功解析 {len(results)} 个回放视频。")
    return results


def export_to_m3u(results, uid, filename="kuaishou_playbacks.m3u"):
    """导出为 APTV 可订阅的 M3U 格式"""
    if not results:
        print("[-] 没有数据可导出。")
        return

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for item in results:
            title = f"回放 {item['time']}"
            url = item['url']
            f.write(f'#EXTINF:-1 tvg-name="{title}" group-title="快手回放_{uid}",{title}\n')
            f.write(f"{url}\n")
    print(f"\n[+] 成功！播放列表已保存至: {os.path.abspath(filename)}")


if __name__ == '__main__':
    # 结合 GitHub Actions 使用
    TEST_UID = os.environ.get("KS_UID", "3x9ggz5834mnxve")
    MY_COOKIE = os.environ.get("KS_COOKIE", "")

    if TEST_UID and TEST_UID != "在此填写UID进行本地测试":
        # 不再传递 limit 参数，直接开抓全部
        playbacks = get_all_playbacks(uid=TEST_UID, user_cookie=MY_COOKIE)

        if playbacks:
            export_to_m3u(playbacks, uid=TEST_UID, filename="kuaishou_playbacks.m3u")
    else:
        print("[-] 请配置目标 UID。")
