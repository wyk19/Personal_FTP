import os
import requests
import re
import json
import time
import datetime
import string
import random

def generate_random_did() -> str:
    """生成随机设备指纹"""
    chars = string.ascii_lowercase + string.digits
    random_hash = ''.join(random.choices(chars, k=32))
    return f"web_{random_hash}"


def resolve_single_playback(video_id: str):
    """解析单条回放视频 - 强化抗连接重置版 (返回 url, error_msg)"""
    url = f"https://live.kuaishou.com/playback/{video_id}"
    env_cookie = os.environ.get('KS_COOKIE', '')
    cookie_str = env_cookie if env_cookie else f"did={generate_random_did()};"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1",
        "Cookie": cookie_str,
        "Connection": "close"  # 降低被防火墙强行掐断的概率
    }

    # 最多重试 3 次
    for attempt in range(3):
        try:
            sess = requests.Session()
            sess.trust_env = False
            requests.packages.urllib3.disable_warnings()
            
            resp = sess.get(url, headers=headers, verify=False, timeout=5.0) # Actions里时间可以稍微给宽裕点
            html = resp.text

            # 拦截检测
            if "did=" not in html and "playbackInfo" not in html:
                if attempt < 2:
                    time.sleep(1.5) # 被拦截了多等一会儿
                    continue
                return None, "被风控拦截(需配置真实Cookie)"

            target_html = html.split('"playbackInfo"')[-1] if '"playbackInfo"' in html else html

            json_url_match = re.search(r'"url":"(https?://[^"]+?\.m3u8[^"]*?)"', target_html)
            if json_url_match: return json_url_match.group(1).replace(r'\u002F', '/').replace(r'\/', '/'), ""

            json_hls_match = re.search(r'"hlsUrl":"(https?://[^"]+?\.m3u8[^"]*?)"', target_html)
            if json_hls_match: return json_hls_match.group(1).replace(r'\u002F', '/').replace(r'\/', '/'), ""

            m_all = re.findall(r'(https?://[^\s"\'\\]+?\.m3u8[^\s"\'\\]*)', target_html)
            if m_all: return m_all[0].replace(r'\u002F', '/').replace(r'\/', '/'), ""

            return None, "正则未匹配到源地址"

        except requests.exceptions.Timeout:
            if attempt < 2:
                time.sleep(1.0)
                continue
            return None, "请求超时(Timeout)"
            
        except requests.exceptions.ConnectionError:
            # 捕获 104 Connection reset by peer
            if attempt < 2:
                time.sleep(2.0) # 避避风头
                continue
            return None, "连接被服务器强行阻断(104)"
            
        except Exception as e:
            return None, f"异常: {e}"

    return None, "重试耗尽"


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
    seen_vids = set()  # 防重复记录池
    page_count = 1

    while True:
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
                
                # 去重拦截
                if vid in seen_vids:
                    print(f"  -> [去重] 跳过重复视频: {vid}")
                    continue
                seen_vids.add(vid)

                ts_ms = obj.get('createTime', 0)
                try:
                    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
                    dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=tz_bj).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    dt = "未知时间"

                print(f"  -> 解析中 ({len(results) + 1}): {dt} (ID:{vid})")
                
                # 💡 核心修改：接收增强版函数返回的两个值
                real_m3u8, err_msg = resolve_single_playback(vid)

                if real_m3u8:
                    results.append({"vid": vid, "time": dt, "url": real_m3u8})
                    print(f"     [+] 成功获取地址。")
                else:
                    # 如果重试3次依然失败，在控制台打印真实死因
                    print(f"     [❌] 失败: {err_msg}")

                # 每次解析完休息一下，防止被封 IP
                time.sleep(1.0)

            pcursor = inner.get('pcursor')
            if not pcursor or pcursor == "no_more":
                print("[*] 服务器返回 no_more，已到达历史记录最末尾。")
                break

            params['cursor'] = pcursor
            page_count += 1
            time.sleep(1.5)

        except Exception as e:
            print(f"[-] 列表请求异常: {e}")
            break

    print(f"\n[*] 任务结束，共成功解析 {len(results)} 个无重复回放视频。")
    return results


def export_to_m3u(results, uid, filename="kuaishou_playbacks.m3u"):
    """导出为 SenPlayer 等可订阅的 M3U 格式"""
    if not results:
        print("[-] 没有数据可导出。")
        return

    with open(filename, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for item in results:
            title = f"回放 {item['time']}"
            url = item['url']
            f.write(f'#EXTINF:-1 vod="1" tvg-name="{title}",{title}\n')
            f.write(f"{url}\n")
    print(f"\n[+] 成功！播放列表已保存至: {os.path.abspath(filename)}")


if __name__ == '__main__':
    TEST_UID = os.environ.get("KS_UID", "3x9ggz5834mnxve")
    MY_COOKIE = os.environ.get("KS_COOKIE", "")

    if TEST_UID and TEST_UID != "在此填写UID进行本地测试":
        playbacks = get_all_playbacks(uid=TEST_UID, user_cookie=MY_COOKIE)

        if playbacks:
            export_to_m3u(playbacks, uid=TEST_UID, filename="kuaishou_playbacks.m3u")
    else:
        print("[-] 请配置目标 UID。")
