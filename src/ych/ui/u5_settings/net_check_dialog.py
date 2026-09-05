# 网络检测对话框：网站延迟测试（并行、色彩分级）+ IP 归属信息。
# 所有网络 IO 在 LlmWorker 后台线程执行——严禁阻塞 GUI 线程（旧版假死根因）。
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ych.services.s4_net.http_client import HttpClient
from ych.services.s4_net.ip_info import fetch_ip_info
from ych.ui.u6_common.llm_worker import LlmWorker

# (显示名, 探测地址)；Pexels/Pixabay 同时用于"能否访问国外素材站"结论
_SITES: tuple[tuple[str, str], ...] = (
    ("Apple", "https://www.apple.com"),
    ("GitHub", "https://github.com"),
    ("Google", "https://www.google.com"),
    ("YouTube", "https://www.youtube.com"),
    ("Pexels", "https://www.pexels.com"),
    ("Pixabay", "https://www.pixabay.com"),
)

_GREEN, _ORANGE, _RED, _GRAY = "#16a34a", "#d97706", "#dc2626", "#6b7280"


def _latency_color(ms: int) -> str:
    if ms < 300:
        return _GREEN
    if ms < 1000:
        return _ORANGE
    return _RED


class NetCheckDialog(QDialog):
    """网络检测面板：打开即自动并行测延迟 + 查询 IP 信息。"""

    def __init__(self, http: HttpClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._http = http
        self._workers: list[LlmWorker] = []
        self.setWindowTitle(self.tr("网络检测"))
        self.resize(720, 520)
        root = QVBoxLayout(self)

        # ---- 网站测试 ----
        sites_box = QGroupBox(self.tr("网站测试"))
        sites_lay = QVBoxLayout(sites_box)
        head = QHBoxLayout()
        head.addWidget(QLabel(self.tr("数值为连接延迟（毫秒）")))
        head.addStretch(1)
        self.btn_sites = QPushButton(self.tr("重新测试"))
        self.btn_sites.setObjectName("secondaryBtn")
        self.btn_sites.clicked.connect(self.start_site_test)
        head.addWidget(self.btn_sites)
        sites_lay.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self._site_labels: dict[str, QLabel] = {}
        for idx, (name, _url) in enumerate(_SITES):
            grid.addWidget(QLabel(name), idx // 3, (idx % 3) * 2)
            status = QLabel("—")
            status.setStyleSheet(f"color: {_GRAY};")
            self._site_labels[name] = status
            grid.addWidget(status, idx // 3, (idx % 3) * 2 + 1)
        for col in (1, 3, 5):
            grid.setColumnStretch(col, 1)
        sites_lay.addLayout(grid)
        self.conclusion_label = QLabel("")
        self.conclusion_label.setStyleSheet(f"color: {_GRAY};")
        sites_lay.addWidget(self.conclusion_label)
        root.addWidget(sites_box)

        # ---- IP 信息 ----
        ip_box = QGroupBox(self.tr("IP 信息"))
        form = QFormLayout(ip_box)
        self.ip_labels: dict[str, QLabel] = {}
        rows: tuple[tuple[str, str], ...] = (
            ("ip", self.tr("IP 地址")),
            ("country", self.tr("国家 / 地区")),
            ("isp", self.tr("服务商 (ISP)")),
            ("org", self.tr("组织")),
            ("location", self.tr("位置")),
            ("asn", self.tr("自治域")),
            ("timezone", self.tr("时区")),
        )
        for key, label in rows:
            value = QLabel("—")
            self.ip_labels[key] = value
            form.addRow(label, value)
        ip_lay_row = QHBoxLayout()
        refresh_btn = QPushButton(self.tr("刷新"))
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.clicked.connect(self.start_ip_query)
        ip_lay_row.addStretch(1)
        ip_lay_row.addWidget(refresh_btn)
        form.addRow("", ip_lay_row)
        root.addWidget(ip_box)
        root.addStretch(1)

        self.start_site_test()
        self.start_ip_query()

    # ---- 网站测试 ----
    def start_site_test(self) -> None:
        self.btn_sites.setEnabled(False)
        self.conclusion_label.setText(self.tr("测试中…"))
        self.conclusion_label.setStyleSheet(f"color: {_GRAY};")
        for lb in self._site_labels.values():
            lb.setText("…")
            lb.setStyleSheet(f"color: {_GRAY};")

        def _task() -> dict[str, Any]:
            results: dict[str, int | None] = {}
            with ThreadPoolExecutor(max_workers=len(_SITES)) as pool:
                futures = {
                    pool.submit(self._http.probe_latency, url, 4.0): name
                    for name, url in _SITES
                }
                for fut in as_completed(futures):
                    results[futures[fut]] = fut.result()
            return results

        worker = LlmWorker(_task)
        self._workers.append(worker)
        worker.done.connect(self._on_sites_done)
        worker.failed.connect(self._on_sites_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_sites_done(self, result: object) -> None:
        self.btn_sites.setEnabled(True)
        if not isinstance(result, dict):
            return
        for name, status in self._site_labels.items():
            ms = result.get(name)
            if isinstance(ms, int):
                status.setText(f"{ms} ms")
                status.setStyleSheet(
                    f"color: {_latency_color(ms)}; font-weight: bold;"
                )
            else:
                status.setText(self.tr("不可达"))
                status.setStyleSheet(f"color: {_RED}; font-weight: bold;")
        stock_ok = [
            result.get(n) is not None
            for n in ("Pexels", "Pixabay")
        ]
        if any(stock_ok):
            self.conclusion_label.setText(
                self.tr("✓ 可访问国外素材站，可开启国外平台采集。"),
            )
            self.conclusion_label.setStyleSheet(f"color: {_GREEN};")
        else:
            self.conclusion_label.setText(
                self.tr("✗ 当前无法访问国外素材站：请开启 VPN/代理后重新测试。"),
            )
            self.conclusion_label.setStyleSheet(f"color: {_RED};")

    def _on_sites_failed(self, msg: str) -> None:
        self.btn_sites.setEnabled(True)
        self.conclusion_label.setText(self.tr(f"测试失败：{msg}"))
        self.conclusion_label.setStyleSheet(f"color: {_RED};")

    # ---- IP 信息 ----
    def start_ip_query(self) -> None:
        for lb in self.ip_labels.values():
            lb.setText("…")
            lb.setStyleSheet(f"color: {_GRAY};")

        worker = LlmWorker(lambda: fetch_ip_info(self._http))
        self._workers.append(worker)
        worker.done.connect(self._on_ip_done)
        worker.failed.connect(self._on_ip_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ip_done(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        info: dict[str, str] = {k: str(v) for k, v in result.items()}
        self.ip_labels["ip"].setText(info.get("ip", "—"))
        cc = info.get("country_code", "")
        country = info.get("country", "—")
        self.ip_labels["country"].setText(
            f"{country}（{cc}）" if cc else country,
        )
        self.ip_labels["isp"].setText(info.get("isp") or "—")
        self.ip_labels["org"].setText(info.get("org") or "—")
        region = info.get("region", "")
        city = info.get("city", "")
        location = "，".join(p for p in (city, region) if p) or "—"
        self.ip_labels["location"].setText(location)
        self.ip_labels["asn"].setText(info.get("asn") or "—")
        self.ip_labels["timezone"].setText(info.get("timezone") or "—")

    def _on_ip_failed(self, msg: str) -> None:
        for lb in self.ip_labels.values():
            lb.setText(self.tr("获取失败"))
            lb.setStyleSheet(f"color: {_RED};")
        self.ip_labels["ip"].setToolTip(msg)
