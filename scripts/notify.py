#!/usr/bin/env python3
"""
法拍地圖更新通知 — Email 發送
讀取 notify-emails.txt 清單，發送更新通知 + 網站連結
"""

import json
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
EMAILS_FILE = PROJECT_DIR / "notify-emails.txt"
CURRENT_JSON = PROJECT_DIR / "data" / "current.json"
SITE_URL = "https://ai-cooperation.github.io/foreclosure-map/"


def load_emails():
    """從 notify-emails.txt 讀取 email 清單"""
    emails = []
    if not EMAILS_FILE.exists():
        print(f"[ERROR] 找不到 {EMAILS_FILE}")
        return emails
    for line in EMAILS_FILE.read_text().strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            emails.append(line)
    return emails


def load_stats():
    """從 current.json 讀取統計"""
    if not CURRENT_JSON.exists():
        return {}
    with open(CURRENT_JSON) as f:
        data = json.load(f)
    return data.get("meta", {})


def send_email(smtp_host, smtp_port, smtp_user, smtp_pass, to_list, subject, body_html):
    """透過 SMTP 發送 email"""
    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_list, msg.as_string())
    print(f"已發送通知至 {len(to_list)} 個 email")


def main():
    import os

    emails = load_emails()
    if not emails:
        print("[WARN] 無通知對象")
        return

    stats = load_stats()
    week = stats.get("week", datetime.now().strftime("%Y-W%W"))
    total = stats.get("total_count", 0)
    type_counts = stats.get("type_counts", {})
    courts = stats.get("courts_scraped", 0)
    geocoded = stats.get("pre_geocoded", 0)

    land_count = type_counts.get("land", 0)
    building_count = type_counts.get("building", 0)

    subject = f"法拍物件地圖更新 {week} — 共 {total} 筆"

    body_html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>法拍物件地圖 — 本週更新</h2>
        <table style="border-collapse: collapse; width: 100%;">
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>週次</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{week}</td></tr>
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>物件總數</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{total} 筆</td></tr>
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>土地</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{land_count} 筆</td></tr>
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>房屋</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{building_count} 筆</td></tr>
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>已有座標</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{geocoded} 筆</td></tr>
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>法院數</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{courts} 個</td></tr>
        </table>
        <p style="margin-top: 20px;">
            <a href="{SITE_URL}" style="display: inline-block; padding: 12px 24px; background: #2563eb; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
                開啟法拍地圖
            </a>
        </p>
        <p style="color: #888; font-size: 12px; margin-top: 20px;">
            更新時間: {datetime.now().strftime("%Y-%m-%d %H:%M")}<br>
            如需取消通知，請從 notify-emails.txt 移除您的 email。
        </p>
    </div>
    """

    # SMTP 設定 (從環境變數讀取)
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        print(f"[ERROR] 需設定環境變數 SMTP_USER 和 SMTP_PASS")
        print(f"  export SMTP_USER='your@gmail.com'")
        print(f"  export SMTP_PASS='your-app-password'")
        print(f"  (Gmail 需使用應用程式密碼: https://myaccount.google.com/apppasswords)")
        print(f"\n預覽通知內容:")
        print(f"  收件人: {', '.join(emails)}")
        print(f"  主旨: {subject}")
        print(f"  物件: {total} 筆 (土地 {land_count} / 房屋 {building_count})")
        print(f"  連結: {SITE_URL}")
        return

    send_email(smtp_host, smtp_port, smtp_user, smtp_pass, emails, subject, body_html)


if __name__ == "__main__":
    main()
