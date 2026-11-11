# -*- coding: utf-8 -*-
"""MOMO 商品搜尋分析：輸入關鍵字與頁數 -> 爬取 -> 折線圖 / 圓餅圖 / 旭日圖"""
import re
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from curl_cffi import requests  # 模擬 Chrome 的 TLS 指紋，避免被擋

HOME_URL = "https://www.momoshop.com.tw/"
API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}

# 價格帶（圓餅圖與旭日圖共用）
PRICE_EDGES = [0, 300, 500, 1000, 1500, 2000, 3000, 5000, float("inf")]
PRICE_LABELS = [
    "300以下", "300-499", "500-999", "1,000-1,499",
    "1,500-1,999", "2,000-2,999", "3,000-4,999", "5,000以上",
]


# ---------------------------------------------------------------- 爬蟲 (CODE01)
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(ttl=600, show_spinner=False)
def fetch_page(keyword: str, page: int) -> list:
    """抓取單頁商品；失敗會丟出例外（例外不會被快取）。"""
    session = requests.Session(impersonate="chrome")
    session.get(HOME_URL, headers=HEADERS, timeout=20)  # 先取得 cookie
    resp = session.post(
        API_URL, headers=HEADERS, json=build_payload(keyword, page), timeout=20
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}：{resp.text[:200]}")
    return resp.json().get("rtnSearchData", {}).get("goodsInfoList", []) or []


def crawl(keyword: str, pages: int, delay: float) -> pd.DataFrame:
    rows = []
    progress = st.progress(0.0, text="準備開始...")
    for i in range(1, pages + 1):
        progress.progress((i - 1) / pages, text=f"抓取第 {i} / {pages} 頁...")
        products = fetch_page(keyword, i)
        if not products:
            st.info(f"第 {i} 頁沒有資料，已提前結束。")
            break
        rows += [
            {"品名": p.get("goodsName", ""), "價格": p.get("goodsPrice", "")}
            for p in products
        ]
        if i < pages:
            time.sleep(delay)
    progress.progress(1.0, text="完成")
    progress.empty()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 資料整理 (CODE02)
def guess_brand(name: str) -> str:
    """從商品名稱粗略推測品牌：優先取開頭【】或[]內文字，否則取第一個詞。"""
    name = str(name).strip()
    m = re.match(r"^[【\[（(]([^】\]）)]{1,15})[】\]）)]", name)
    if m:
        return m.group(1).strip()
    token = re.split(r"[\s\u3000/／\-－_＿]+", name)[0]
    return token[:12] or "未知"


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # 對應原本的 replace("$","")、replace(",","")、去除非數字
    out["價格"] = pd.to_numeric(
        out["價格"].astype(str).str.replace(r"[^\d]", "", regex=True),
        errors="coerce",
    )
    out = out.dropna(subset=["價格"]).reset_index(drop=True)
    out["價格"] = out["價格"].astype(int)
    out.insert(0, "序號", out.index + 1)
    out["價格帶"] = pd.cut(out["價格"], bins=PRICE_EDGES, labels=PRICE_LABELS, right=False)
    out["品牌(推測)"] = out["品名"].map(guess_brand)
    return out


# ---------------------------------------------------------------- 畫面
st.set_page_config(page_title="MOMO 商品價格分析", layout="wide")
st.title("MOMO 商品價格分析")

with st.form("search_form"):
    c1, c2, c3 = st.columns([3, 1, 1])
    keyword = c1.text_input("產品名稱", value="行動電源")
    pages = c2.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    delay = c3.number_input("每頁間隔(秒)", min_value=0.0, max_value=10.0, value=1.0, step=0.5)
    submitted = st.form_submit_button("開始爬取", type="primary")

if submitted:
    if not keyword.strip():
        st.warning("請輸入產品名稱")
    else:
        try:
            raw = crawl(keyword.strip(), int(pages), float(delay))
        except Exception as e:
            st.error(f"爬取失敗：{e}")
            st.stop()
        df_new = prepare(raw) if not raw.empty else raw
        if df_new.empty:
            st.warning("沒有抓到任何有效商品，請換個關鍵字試試。")
            st.session_state.pop("df", None)
        else:
            st.session_state["df"] = df_new
            st.session_state["keyword"] = keyword.strip()

if "df" not in st.session_state:
    st.info("輸入產品名稱與頁數後，按「開始爬取」。")
    st.stop()

df = st.session_state["df"]
kw = st.session_state["keyword"]

# 統計數字（CODE02 的 mean / max / min）
mean_price = df["價格"].mean()
m1, m2, m3, m4 = st.columns(4)
m1.metric("商品數", f"{len(df):,}")
m2.metric("平均價格", f"{mean_price:,.0f}")
m3.metric("最高價格", f"{df['價格'].max():,.0f}")
m4.metric("最低價格", f"{df['價格'].min():,.0f}")

with st.sidebar:
    st.header("圖表設定")
    n_line = st.slider("折線圖顯示前幾筆", 10, max(len(df), 11), min(70, len(df)))
    top_brand = st.slider("旭日圖顯示前幾大品牌", 3, 15, 8)

tab1, tab2, tab3, tab4 = st.tabs(["折線圖", "圓餅圖", "旭日圖", "資料表"])

# ---- 折線圖 + 平均線（取代原本寫死的 1273）
with tab1:
    fig_line = px.line(
        df.head(n_line), x="序號", y="價格", markers=True, hover_name="品名",
        labels={"序號": "商品序號"},
        title=f"MOMO 電商網站上「{kw}」售價",
    )
    fig_line.add_hline(
        y=mean_price, line_dash="dash", line_color="red",
        annotation_text=f"平均 {mean_price:,.0f}", annotation_position="top left",
    )
    st.plotly_chart(fig_line)

# ---- 圓餅圖：價格帶佔比
with tab2:
    band = df.groupby("價格帶", observed=True).size().reset_index(name="商品數")
    fig_pie = px.pie(band, names="價格帶", values="商品數", title=f"「{kw}」價格帶佔比")
    fig_pie.update_traces(sort=False, textinfo="percent+label")
    st.plotly_chart(fig_pie)

# ---- 旭日圖：價格帶 -> 品牌
with tab3:
    top = df["品牌(推測)"].value_counts().head(top_brand).index
    sb = df.copy()
    sb["品牌(推測)"] = sb["品牌(推測)"].where(sb["品牌(推測)"].isin(top), "其他")
    sb = (
        sb.groupby(["價格帶", "品牌(推測)"], observed=True)
        .size().reset_index(name="商品數")
    )
    fig_sun = px.sunburst(
        sb, path=["價格帶", "品牌(推測)"], values="商品數",
        title=f"「{kw}」價格帶 → 品牌分布",
    )
    st.plotly_chart(fig_sun)
    st.caption("品牌是依商品名稱開頭推測的，可能不完全準確。")

# ---- 資料表與下載
with tab4:
    st.dataframe(df[["序號", "品名", "價格", "價格帶", "品牌(推測)"]], hide_index=True)
    st.download_button(
        "下載 CSV",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"MOMO_{datetime.now():%Y%m%d}_{kw}.csv",
        mime="text/csv",
    )
