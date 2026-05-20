from typing import List
from models.deal import Deal


class ScoringService:
    @staticmethod
    def calculate_score(deal: Deal) -> float:
        """
        Calcula o peso (score) de uma oferta.
        Ofertas com score maior vão primeiro pro Telegram.
        """
        score = 0.0

        # 1. Peso de Desconto (Gatilho principal)
        if deal.original_price and deal.original_price > 0 and deal.price < deal.original_price:
            discount_pct = (deal.original_price - deal.price) / deal.original_price
            score += (discount_pct * 100) * 2  # Desconto vale dobro (ex: 50% = 100 pts)

        # 2. Peso de Preço Psicológico (Achadinhos baratos convertem mais)
        if deal.price <= 30.00:
            score += 50
        elif deal.price <= 50.00:
            score += 30
        elif deal.price <= 100.00:
            score += 10

        # 3. Qualidade do Título
        title_upper = deal.title.upper()
        if "FRETE GRÁTIS" in title_upper or "FRETE GRATIS" in title_upper:
            score += 25
        if "LEVE" in title_upper and "PAGUE" in title_upper:
            score += 20

        return round(score, 2)

    @classmethod
    def rank_deals(cls, deals: List[Deal]) -> List[Deal]:
        """Ordena a lista de ofertas do maior para o menor score."""
        for deal in deals:
            # Assumindo que você adicione um campo opcional `score` no model Deal
            deal.score = cls.calculate_score(deal)

        return sorted(deals, key=lambda d: getattr(d, 'score', 0), reverse=True)