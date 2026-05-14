import matplotlib.pyplot as plt
import os
import pandas as pd
from datetime import datetime

class VisualDataAgent:
    def __init__(self, output_dir="assets_cache/charts"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        plt.style.use('dark_background')

    def generate_stock_chart(self, symbol, data_points, title="Market Performance"):
        """Gera um grafico de linha profissional para dados de mercado."""
        plt.figure(figsize=(10, 6))
        plt.plot(data_points, color='#00ff00', linewidth=2, marker='o')
        plt.title(f"{title} ({symbol})", fontsize=16, color='#ffffff', pad=20)
        plt.grid(color='#333333', linestyle='--')
        plt.xlabel("Timeline", color='#888888')
        plt.ylabel("Value", color='#888888')
        
        # Estetica Premium
        plt.gca().spines['top'].set_visible(False)
        plt.gca().spines['right'].set_visible(False)
        
        path = os.path.join(self.output_dir, f"chart_{symbol}_{int(datetime.now().timestamp())}.png")
        plt.savefig(path, transparent=True, dpi=150)
        plt.close()
        print(f"📊 [VISUAL AGENT] Grafico gerado: {path}")
        return path

    def generate_roi_comparison(self, items, values, title="ROI Comparison"):
        """Gera um grafico de barras para comparacao de ganhos/investimentos."""
        plt.figure(figsize=(10, 6))
        colors = ['#4CAF50', '#2196F3', '#FFC107', '#E91E63']
        plt.bar(items, values, color=colors[:len(items)])
        plt.title(title, fontsize=16, color='#ffffff', pad=20)
        
        path = os.path.join(self.output_dir, f"roi_{int(datetime.now().timestamp())}.png")
        plt.savefig(path, transparent=True, dpi=150)
        plt.close()
        print(f"📊 [VISUAL AGENT] Comparativo ROI gerado: {path}")
        return path

if __name__ == "__main__":
    agent = VisualDataAgent()
    # Teste para o mercado US
    agent.generate_stock_chart("NVDA", [100, 120, 115, 140, 160], "NVIDIA Growth 2026")
    # Teste para o mercado DE
    agent.generate_roi_comparison(["Traditional", "AI Automation"], [5, 45], "Annual ROI % (Germany)")
