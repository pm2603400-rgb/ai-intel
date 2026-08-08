name: Weekly Report

on:
  schedule:
    # 每週一 UTC 01:00（台灣時間週一早上 9 點）生成「上週一~上週日」的週報
    - cron: '0 1 * * 1'
  workflow_dispatch:      # 也可手動觸發（補生成）
    inputs:
      week_end:
        description: '補跑指定週：填該週的「週日」日期 YYYY-MM-DD（留空=自動抓上一整週）'
        required: false
        default: ''

jobs:
  weekly:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Generate weekly report
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          WEEK_END_OVERRIDE: ${{ github.event.inputs.week_end }}
        run: python generate_weekly.py
