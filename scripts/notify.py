#!/usr/bin/env python3
"""
法拍地圖更新通知 — Email 發送
讀取 notify-emails.txt 清單，發送更新通知 + 網站連結
支援 --status ok/error 和 --error-message 參數，用於失敗通知
"""

import argparse
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
    """從環境變數 NOTIFY_EMAILS 或 notify-emails.txt 讀取 email 清單"""
    import os
    env_emails = os.environ.get("NOTIFY_EMAILS", "")
    if env_emails:
        return [e.strip() for e in env_emails.split(",") if e.strip()]
    emails = []
    if not EMAILS_FILE.exists():
        print(f"[WARN] 未設定 NOTIFY_EMAILS 環境變數，也找不到 {EMAILS_FILE}")
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


def build_success_email(stats):
    """產生成功通知的主旨和內容"""
    week = stats.get("week", datetime.now().strftime("%Y-W%W"))
    total = stats.get("total_count", 0)
    type_counts = stats.get("type_counts", {})
    courts = stats.get("courts_scraped", 0)
    geocoded = stats.get("geocode_success", stats.get("pre_geocoded", 0))
    land_count = stats.get("land_count", type_counts.get("land", 0))
    building_count = stats.get("building_count", type_counts.get("building", 0))

    subject = f"法拍物件地圖更新 {week} — 共 {total} 筆"

    body_html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #16a34a;">✅ 法拍物件地圖 — 本週更新</h2>
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
    return subject, body_html


def build_error_email(stats, error_message):
    """產生失敗通知的主旨和內容"""
    week = stats.get("week", datetime.now().strftime("%Y-W%W"))

    subject = f"⚠️ 法拍物件地圖更新失敗 {week}"

    body_html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #dc2626;">❌ 法拍物件地圖 — 更新失敗</h2>
        <table style="border-collapse: collapse; width: 100%;">
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>週次</b></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{week}</td></tr>
            <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>錯誤訊息</b></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #dc2626;">{error_message}</td></tr>
        </table>
        <p style="margin-top: 16px; padding: 12px; background: #fef2f2; border-left: 4px solid #dc2626; border-radius: 4px;">
            本週自動更新未完成，網站資料維持上期。<br>
            請檢查伺服器日誌：<code>/tmp/foreclosure-map-{week}.log</code>
        </p>
        <p style="margin-top: 20px;">
            <a href="{SITE_URL}" style="display: inline-block; padding: 12px 24px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
                查看法拍地圖（上期資料）
            </a>
        </p>
        <p style="color: #888; font-size: 12px; margin-top: 20px;">
            更新時間: {datetime.now().strftime("%Y-%m-%d %H:%M")}<br>
            如需取消通知，請從 notify-emails.txt 移除您的 email。
        </p>
    </div>
    """
    return subject, body_html


def main():
    import os

    parser = argparse.ArgumentParser(description="法拍地圖更新通知")
    parser.add_argument("--status", choices=["ok", "error"], default="ok",
                        help="Pipeline 執行狀態 (ok=成功, error=失敗)")
    parser.add_argument("--error-message", default="",
                        help="失敗時的錯誤訊息")
    args = parser.parse_args()

    emails = load_emails()
    if not emails:
        print("[WARN] 無通知對象")
        return

    stats = load_stats()

    if args.status == "error":
        subject, body_html = build_error_email(stats, args.error_message or "未知錯誤")
    else:
        subject, body_html = build_success_email(stats)

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
        print(f"  狀態: {args.status}")
        print(f"  主旨: {subject}")
        if args.status == "error":
            print(f"  錯誤: {args.error_message}")
        print(f"  連結: {SITE_URL}")
        return

    send_email(smtp_host, smtp_port, smtp_user, smtp_pass, emails, subject, body_html)


if __name__ == "__main__":
    main()
