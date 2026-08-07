"""Weighted Dixon-Coles model (SPEC.md §29-31).

Parameterisation (§29.1):
    log(λ_home) = μ + γ + α_home + β_away
    log(λ_away) = μ + α_away + β_home

`β` is defensive *weakness* — it adds to the opponent's expected goals; a
strong defence has a negative β. Identifiability: mean(α) = mean(β) = 0,
enforced by expressing the last team's parameter as minus the sum of the
others.

Recency weighting (§29.2): w_m = exp(-ξ · days_before_reference). ξ is a
hyperparameter chosen by outer walk-forward validation (backtest.tune_xi),
never fit by this MLE.

Low-score correction (§31): τ(h,a; ρ) on the {0,1}² cells, ρ fit jointly
with μ, γ, α, β by weighted MLE.

Unseen teams (§29.4 limit case): a team with no training history receives
α = β = 0 — the league-average prior with zero evidence. The full
empirical-Bayes promoted-team prior is a later, ablation-gated refinement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..config import DEFAULT_MODEL, ModelConfig


@dataclass(frozen=True)
class DixonColesParams:
    teams: tuple[str, ...]
    mu: float
    gamma: float
    alpha: dict[str, float]
    beta: dict[str, float]
    rho: float
    xi: float
    reference_date: pd.Timestamp
    n_matches: int
    log_likelihood: float
    # §30 Elo covariate coefficient; None when fitted without Elo.
    theta: float | None = None

    def lambdas(
        self,
        home_team: str,
        away_team: str,
        cfg: ModelConfig = DEFAULT_MODEL,
        elo_diff: float | None = None,
    ) -> tuple[float, float]:
        """Expected goals (λ_home, λ_away) for a fixture, clamped to the
        configured plausible range (§30 caps/floors).

        elo_diff is §30's d_Elo = (Elo_home - Elo_away)/400. §30
        missing-feature rule: when the model has θ but no elo_diff is
        supplied (or vice versa), the term is dropped from the log(λ) sum —
        never substituted with a placeholder value.
        """
        a_h = self.alpha.get(home_team, 0.0)
        b_h = self.beta.get(home_team, 0.0)
        a_a = self.alpha.get(away_team, 0.0)
        b_a = self.beta.get(away_team, 0.0)
        elo_term = (
            self.theta * elo_diff
            if self.theta is not None and elo_diff is not None
            else 0.0
        )
        lam_h = np.exp(self.mu + self.gamma + a_h + b_a + elo_term)
        lam_a = np.exp(self.mu + a_a + b_h - elo_term)
        lo, hi = cfg.lambda_floor, cfg.lambda_ceiling
        return float(np.clip(lam_h, lo, hi)), float(np.clip(lam_a, lo, hi))


def _tau_log(
    h: np.ndarray, a: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray, rho: float
) -> np.ndarray:
    """log τ(h,a;ρ) — nonzero only on the {0,1}² cells."""
    tau = np.ones_like(lam_h)
    m00 = (h == 0) & (a == 0)
    m01 = (h == 0) & (a == 1)
    m10 = (h == 1) & (a == 0)
    m11 = (h == 1) & (a == 1)
    tau[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    tau[m01] = 1.0 + lam_h[m01] * rho
    tau[m10] = 1.0 + lam_a[m10] * rho
    tau[m11] = 1.0 - rho
    return np.log(np.clip(tau, 1e-10, None))


def fit(
    matches: pd.DataFrame,
    xi: float | None = None,
    reference_date: pd.Timestamp | None = None,
    fix_rho_zero: bool = False,
    use_elo: bool = False,
    param_priors: dict[str, "PriorSpec"] | None = None,
    season_decay: float = 1.0,
    reference_season: int | None = None,
    cfg: ModelConfig = DEFAULT_MODEL,
) -> DixonColesParams:
    """Weighted MLE over μ, γ, α[], β[], ρ — and θ when use_elo (§29.1,
    §30, §31).

    `fix_rho_zero=True` yields the pure independent-Poisson baseline for
    §28's "Poisson vs Dixon-Coles" ablation. `use_elo=True` requires
    pre-match elo_home/elo_away columns (model.elo.compute) and fits §30's
    single constrained coefficient θ (= θ_H = θ_A) on
    d_Elo = (Elo_home - Elo_away)/400 — the "DC vs DC+Elo" ablation is
    exactly this flag.

    `param_priors` (§29.4): per-team Gaussian priors on (α, β), applied as
    a penalty on the weighted log-likelihood. The likelihood mass grows
    with every match the team plays while the penalty stays fixed, so
    shrinkage toward the prior fades continuously with evidence.

    `season_decay` (§29.5): extra weight multiplier `season_decay^(seasons
    before reference_season)` on top of the daily ξ-decay — the
    full-refit-architecture translation of "shrink previous-season
    strength at the boundary". 1.0 = disabled. Requires a `season` column
    and `reference_season` when enabled.
    """
    if xi is None:
        xi = cfg.xi
    if reference_date is None:
        reference_date = matches["date"].max()

    teams = tuple(sorted(set(matches["home_team"]) | set(matches["away_team"])))
    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}

    hi = matches["home_team"].map(idx).to_numpy()
    ai = matches["away_team"].map(idx).to_numpy()
    hg = matches["home_goals"].to_numpy(dtype=float)
    ag = matches["away_goals"].to_numpy(dtype=float)
    days = (reference_date - matches["date"]).dt.days.to_numpy(dtype=float)
    w = np.exp(-xi * np.clip(days, 0.0, None))
    if season_decay != 1.0:
        if "season" not in matches.columns or reference_season is None:
            raise ValueError(
                "season_decay needs a `season` column and reference_season"
            )
        seasons_ago = np.clip(
            reference_season - matches["season"].to_numpy(dtype=float), 0.0, None
        )
        w = w * season_decay**seasons_ago
    d_elo = (
        (matches["elo_home"] - matches["elo_away"]).to_numpy(dtype=float) / 400.0
        if use_elo
        else np.zeros(len(matches))
    )

    n_free = 2 * n  # mu, gamma, (n-1) alphas, (n-1) betas
    theta_pos = n_free if use_elo else None
    rho_pos = n_free + (1 if use_elo else 0) if not fix_rho_zero else None

    def unpack(vec: np.ndarray):
        mu, gamma = vec[0], vec[1]
        alpha = np.append(vec[2 : 1 + n], -vec[2 : 1 + n].sum())
        beta = np.append(vec[1 + n : 2 * n], -vec[1 + n : 2 * n].sum())
        theta = vec[theta_pos] if theta_pos is not None else 0.0
        rho = vec[rho_pos] if rho_pos is not None else 0.0
        return mu, gamma, alpha, beta, theta, rho

    # §29.4 prior penalty arrays (teams present in both fit and priors).
    prior_teams = [t for t in (param_priors or {}) if t in idx]
    if prior_teams:
        p_idx = np.array([idx[t] for t in prior_teams])
        pm_a = np.array([param_priors[t].mean_alpha for t in prior_teams])
        pv_a = np.array([param_priors[t].var_alpha for t in prior_teams])
        pm_b = np.array([param_priors[t].mean_beta for t in prior_teams])
        pv_b = np.array([param_priors[t].var_beta for t in prior_teams])

    def nll(vec: np.ndarray) -> float:
        mu, gamma, alpha, beta, theta, rho = unpack(vec)
        log_lh = mu + gamma + alpha[hi] + beta[ai] + theta * d_elo
        log_la = mu + alpha[ai] + beta[hi] - theta * d_elo
        lam_h, lam_a = np.exp(log_lh), np.exp(log_la)
        ll = hg * log_lh - lam_h + ag * log_la - lam_a
        ll += _tau_log(hg, ag, lam_h, lam_a, rho)
        total = -float((w * ll).sum())
        if prior_teams:
            total += float(
                ((alpha[p_idx] - pm_a) ** 2 / (2.0 * pv_a)).sum()
                + ((beta[p_idx] - pm_b) ** 2 / (2.0 * pv_b)).sum()
            )
        return total

    n_params = n_free + (1 if use_elo else 0) + (0 if fix_rho_zero else 1)
    vec0 = np.zeros(n_params)
    vec0[0] = np.log(max(0.5 * (hg.mean() + ag.mean()), 0.2))
    vec0[1] = 0.25
    bounds: list[tuple[float | None, float | None]] = [(None, None)] * n_free
    if use_elo:
        bounds.append((None, None))
    if not fix_rho_zero:
        vec0[rho_pos] = -0.05
        bounds.append(cfg.rho_bounds)

    res = minimize(nll, vec0, method="L-BFGS-B", bounds=bounds)
    mu, gamma, alpha, beta, theta, rho = unpack(res.x)
    return DixonColesParams(
        teams=teams,
        mu=float(mu),
        gamma=float(gamma),
        alpha={t: float(alpha[idx[t]]) for t in teams},
        beta={t: float(beta[idx[t]]) for t in teams},
        rho=float(rho),
        xi=xi,
        reference_date=reference_date,
        n_matches=len(matches),
        log_likelihood=-float(res.fun),
        theta=float(theta) if use_elo else None,
    )
