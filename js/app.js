// 法拍物件地圖 - 前端邏輯

let map;
let markerCluster;
let allFeatures = [];
let currentMarkers = [];
let landPolygons = [];

// 初始化地圖
function initMap() {
    map = L.map("map").setView([23.7, 120.9], 8);

    // NLSC 國土測繪底圖 (免費免 key)
    L.tileLayer(
        "https://wmts.nlsc.gov.tw/wmts/EMAP5_OPENDATA/default/GoogleMapsCompatible/{z}/{y}/{x}",
        {
            maxZoom: 18,
            attribution: '&copy; <a href="https://maps.nlsc.gov.tw/">NLSC</a>',
        }
    ).addTo(map);

    // Marker 聚合群組
    markerCluster = L.markerClusterGroup({
        maxClusterRadius: 50,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
    });
    map.addLayer(markerCluster);
}

// 自訂 marker icon
function createIcon(type) {
    const color = type === "land" ? "#4CAF50" : "#F44336";
    const border = type === "land" ? "#2E7D32" : "#C62828";

    return L.divIcon({
        className: "",
        html: `<div style="
            background:${color};
            border:2px solid ${border};
            border-radius:50%;
            width:14px;
            height:14px;
        "></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
    });
}

// 格式化價格
function formatPrice(price) {
    if (!price) return "-";
    if (price >= 100000000) {
        return (price / 100000000).toFixed(2) + " 億";
    } else if (price >= 10000) {
        return (price / 10000).toFixed(0) + " 萬";
    }
    return price.toLocaleString();
}

// 建立 popup 內容
function createPopup(f) {
    const roundText = f.auction_round ? `第${f.auction_round}拍` : "-";
    const deliveryText = f.delivery === "yes" ? "是" : f.delivery === "no" ? "否" : "-";
    const vacantLabel = f.type === "land" ? "空地" : "空屋";
    const vacantText = f.vacant === "yes" ? "是" : "-";
    const typeText = f.type === "land" ? "土地" : f.type === "building" ? "房屋" : "其他";

    let historyHtml = "";
    if (f.history && f.history.rounds && f.history.rounds.length > 1) {
        const rounds = f.history.rounds
            .map((r) => `${r.week} 第${r.round}拍 ${formatPrice(r.price)}`)
            .join("<br>");
        historyHtml = `<div class="history-info">歷史:<br>${rounds}</div>`;
    } else if (f.history && f.history.first_seen) {
        historyHtml = `<div class="history-info">首次出現: ${f.history.first_seen}</div>`;
    }

    const detailLink = f.detail_url
        ? `<a href="${f.detail_url}" target="_blank">&#128196; 公告原文</a>`
        : "";

    return `<div class="popup-content">
        <h3>${f.court || ""}</h3>
        <div class="case-no">${f.case_no || ""}</div>
        <table>
            <tr><td>類型</td><td>${typeText}</td></tr>
            <tr><td>坐落</td><td>${f.location || "-"}</td></tr>
            <tr><td>面積</td><td>${f.area ? f.area + " ㎡" : "-"}</td></tr>
            <tr><td>底價</td><td class="price">NT$ ${formatPrice(f.min_price)}</td></tr>
            <tr><td>拍別</td><td>${roundText}</td></tr>
            <tr><td>拍賣日</td><td>${f.auction_date || "-"}</td></tr>
            <tr><td>權利範圍</td><td>${f.rrange || "-"}</td></tr>
            <tr><td>點交</td><td>${deliveryText}</td></tr>
            <tr><td>${vacantLabel}</td><td>${vacantText}</td></tr>
        </table>
        ${historyHtml}
        ${detailLink}
    </div>`;
}

// 顯示物件到地圖
function displayFeatures(features) {
    // 清除舊的
    markerCluster.clearLayers();
    landPolygons.forEach((p) => map.removeLayer(p));
    landPolygons = [];
    currentMarkers = [];

    features.forEach((f) => {
        if (!f.coordinates) return;

        const lat = f.coordinates.lat;
        const lng = f.coordinates.lng;
        if (!lat || !lng) return;

        // Marker
        const marker = L.marker([lat, lng], {
            icon: createIcon(f.type),
        });
        marker.bindPopup(createPopup(f), { maxWidth: 280 });
        markerCluster.addLayer(marker);
        currentMarkers.push(marker);

        // 土地物件加畫地籍邊界
        if (
            f.type === "land" &&
            f.coordinates.polygon &&
            f.coordinates.polygon.length > 0
        ) {
            try {
                // twland GeoJSON coordinates: [lng, lat]
                const coords = f.coordinates.polygon[0][0].map((c) => [c[1], c[0]]);
                const polygon = L.polygon(coords, {
                    color: "#4CAF50",
                    weight: 2,
                    opacity: 0.7,
                    fillColor: "#81C784",
                    fillOpacity: 0.3,
                });
                polygon.bindPopup(createPopup(f), { maxWidth: 280 });
                polygon.addTo(map);
                landPolygons.push(polygon);
            } catch (e) {
                // polygon 格式錯誤，跳過
            }
        }
    });

    // 更新統計
    updateStats(features);
}

// 更新統計資訊
function updateStats(features) {
    const land = features.filter((f) => f.type === "land").length;
    const building = features.filter((f) => f.type === "building").length;
    const withCoords = features.filter((f) => f.coordinates).length;

    document.getElementById("stats").innerHTML = `
        <strong>顯示: ${withCoords} 筆</strong><br>
        土地: ${land} 筆 / 房屋: ${building} 筆<br>
        (全部 ${allFeatures.length} 筆, ${allFeatures.length - withCoords} 筆無座標)
    `;
}

// 篩選
function applyFilters() {
    const typeFilter = document.getElementById("filter-type").value;
    const courtFilter = document.getElementById("filter-court").value;
    const countyFilter = document.getElementById("filter-county").value;
    const roundFilter = document.getElementById("filter-round").value;
    const deliveryFilter = document.getElementById("filter-delivery").value;
    const rrangeFilter = document.getElementById("filter-rrange").value;
    const priceMin = document.getElementById("filter-price-min").value;
    const priceMax = document.getElementById("filter-price-max").value;

    let filtered = allFeatures.filter((f) => {
        if (typeFilter && f.type !== typeFilter) return false;
        if (courtFilter && f.court !== courtFilter) return false;
        if (countyFilter && f.county !== countyFilter) return false;
        if (deliveryFilter && f.delivery !== deliveryFilter) return false;
        if (rrangeFilter === "full" && f.rrange !== "全部") return false;
        if (rrangeFilter === "partial" && f.rrange === "全部") return false;

        if (roundFilter) {
            if (roundFilter === "5+") {
                if (!f.auction_round || f.auction_round < 5) return false;
            } else {
                if (f.auction_round !== parseInt(roundFilter)) return false;
            }
        }

        if (priceMin && f.min_price && f.min_price < parseInt(priceMin) * 10000)
            return false;
        if (priceMax && f.min_price && f.min_price > parseInt(priceMax) * 10000)
            return false;

        return true;
    });

    displayFeatures(filtered);

    // 自動 fit bounds
    if (currentMarkers.length > 0) {
        const group = L.featureGroup(currentMarkers);
        map.fitBounds(group.getBounds().pad(0.1));
    }
}

// 重設篩選
function resetFilters() {
    document.getElementById("filter-type").value = "";
    document.getElementById("filter-court").value = "";
    document.getElementById("filter-county").value = "";
    document.getElementById("filter-round").value = "";
    document.getElementById("filter-delivery").value = "";
    document.getElementById("filter-rrange").value = "";
    document.getElementById("filter-price-min").value = "";
    document.getElementById("filter-price-max").value = "";
    displayFeatures(allFeatures);
    map.setView([23.7, 120.9], 8);
}

// 填充篩選選單
function populateFilters(features) {
    const courts = [...new Set(features.map((f) => f.court).filter(Boolean))].sort();
    const counties = [...new Set(features.map((f) => f.county).filter(Boolean))].sort();

    const courtSel = document.getElementById("filter-court");
    courts.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c.replace("臺灣", "").replace("福建", "");
        courtSel.appendChild(opt);
    });

    const countySel = document.getElementById("filter-county");
    counties.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        countySel.appendChild(opt);
    });
}

// 側邊欄切換 (手機)
function toggleSidebar() {
    document.getElementById("sidebar").classList.toggle("open");
}

// 載入資料
async function loadData() {
    try {
        const resp = await fetch("data/current.json");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        allFeatures = data.features || [];

        // 更新 meta 資訊
        const meta = data.meta || {};
        document.getElementById("meta-info").innerHTML = `
            更新: ${meta.week || "-"}<br>
            ${meta.generated_at ? new Date(meta.generated_at).toLocaleString("zh-TW") : ""}
        `;

        // 填充篩選選單
        populateFilters(allFeatures);

        // 顯示全部
        displayFeatures(allFeatures);

        // 如果有物件，fit bounds
        if (currentMarkers.length > 0) {
            const group = L.featureGroup(currentMarkers);
            map.fitBounds(group.getBounds().pad(0.1));
        }
    } catch (err) {
        console.error("載入資料失敗:", err);
        document.getElementById("stats").innerHTML =
            '<span style="color:red">資料載入失敗</span>';
    }
}

// 啟動
initMap();
loadData();
