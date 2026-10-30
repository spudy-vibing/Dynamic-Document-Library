"""Judge calibration against human labels (WP-018, EVAL-13, EVALS_PROPOSAL §6.3).

    pack.py       stratified, seeded sampling of pairs and documents; blinded labelling packs
    labels.py     import and validation of completed labels (one labeller per file)
    agreement.py  agreement statistics: raw agreement, Cohen's kappa, quadratic-weighted kappa,
                  within-1 agreement, cluster-bootstrap CIs
    calibrate.py  judge-vs-human and human-vs-human agreement, thresholds, calibration report
"""
