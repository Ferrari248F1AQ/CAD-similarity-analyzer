# -*- coding: utf-8 -*-
"""
Regression tests for multi-model feature extraction.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import solid_edge_similarity_v2 as se


class MultiModelFeatureExtractionTest(unittest.TestCase):
    def test_extract_signature_uses_all_models_and_feature_types(self):
        mocked_data = {
            'properties': {},
            'feature_list': [
                {'index': 1, 'model_index': 1, 'name': 'RevolvedProtrusion_1', 'type': 'RevolvedProtrusion'},
                {'index': 2, 'model_index': 1, 'name': 'Hole_4', 'type': 'Hole'},
                {'index': 3, 'model_index': 2, 'name': 'ExtrudedProtrusion_1', 'type': 'ExtrudedProtrusion'},
                {'index': 4, 'model_index': 2, 'name': 'Pattern_2', 'type': 'Pattern'},
            ],
            'feature_types': {
                'RevolvedProtrusion': 1,
                'Hole': 1,
                'ExtrudedProtrusion': 1,
                'Pattern': 1,
            },
            'collections': {
                'ExtrudedProtrusions': 99,
                'Holes': 0,
            },
            'sketches_count': 0,
            'sketches_data': [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            filepath = Path(tmp_dir) / 'multi_model.par'
            filepath.write_bytes(b'test')
            with patch.object(se, 'extract_features_via_com', return_value=mocked_data):
                sig = se.extract_signature(filepath)

        self.assertEqual(sig.feature_count, 4)
        self.assertEqual(
            sig.feature_sequence,
            ['RevolvedProtrusion', 'Hole', 'ExtrudedProtrusion', 'Pattern'],
        )
        self.assertEqual(sig.extrusions_count, 1)
        self.assertEqual(sig.holes_count, 1)

    def test_signature_with_only_sketch_payload_is_not_treated_as_failure(self):
        sig = se.FeatureSignature(filename='only_sketch.par', filepath='x', file_hash='h')
        sig.sketches_data = [{'name': 'Sketch1', 'geometry_count': 2, 'constraint_count': 1}]
        sig.total_2d_geometry_count = 2
        sig.total_2d_constraint_count = 1

        self.assertFalse(se._is_signature_extraction_failed(sig))

    def test_analyze_directory_keeps_sketch_only_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            file_ok = root / 'sketch_only.par'
            file_fail = root / 'failed.par'
            file_ok.write_bytes(b'ok')
            file_fail.write_bytes(b'fail')

            def fake_extract_signature(filepath, app=None):
                sig = se.FeatureSignature(
                    filename=filepath.name,
                    filepath=str(filepath),
                    file_hash='h',
                )
                if filepath.name == 'sketch_only.par':
                    sig.sketches_data = [{'name': 'S1', 'geometry_count': 1, 'constraint_count': 0}]
                    sig.total_2d_geometry_count = 1
                    sig.sketches_count = 1
                return sig

            with patch.object(se, 'extract_signature', side_effect=fake_extract_signature):
                signatures = se.analyze_directory(root, use_com=False)

        names = sorted(sig.filename for sig in signatures)
        self.assertEqual(names, ['sketch_only.par'])


class ConstraintCoverageUnavailableTest(unittest.TestCase):
    """Tests for the constraint-coverage-based unavailability of criteria 11 & 12."""

    def _make_sig(self, sketches_data):
        """Helper: crea una FeatureSignature minimale con i dati sketch forniti."""
        sig = se.FeatureSignature(filename='test.par', filepath='test.par', file_hash='h')
        sig.sketches_data = sketches_data
        # Aggrega i tipi di vincolo (come farebbe extract_signature)
        from collections import Counter
        all_constr = Counter()
        total_constr = 0
        total_geom = 0
        for sk in sketches_data:
            total_constr += sk.get('constraint_count', 0)
            total_geom += sk.get('geometry_count', 0)
            all_constr.update(sk.get('constraint_types', {}))
        sig.constraint_2d_types = dict(all_constr)
        sig.total_2d_constraint_count = total_constr
        sig.total_2d_geometry_count = total_geom
        sig.constraint_to_geometry_ratio = total_constr / max(total_geom, 1)
        sig.geometry_2d_types = {'Line2d': total_geom}
        return sig

    def test_criteria_available_when_all_sketches_have_constraints(self):
        """Se tutti gli sketch hanno vincoli -> criteri 11 & 12 calcolati."""
        sketches = [
            {'name': f'S{i}', 'geometry_count': 4, 'constraint_count': 3,
             'constraint_types': {'Coincidente': 3}, 'geometry_types': {'Line2d': 4}}
            for i in range(3)
        ]
        sig1 = self._make_sig(sketches)
        sig2 = self._make_sig(sketches)
        scores = se.compute_raw_scores(sig1, sig2)
        unavailable = scores.get('_unavailable_criteria', [])
        self.assertNotIn('constraint_2d_similarity', unavailable)
        self.assertNotIn('constraint_ratio_similarity', unavailable)
        self.assertIn('constraint_2d_similarity', scores)
        self.assertIn('constraint_ratio_similarity', scores)

    def test_criteria_unavailable_when_no_sketch_has_constraints(self):
        """Se nessuno sketch ha vincoli -> criteri 11 & 12 unavailable."""
        sketches = [
            {'name': f'S{i}', 'geometry_count': 4, 'constraint_count': 0,
             'constraint_types': {}, 'geometry_types': {'Line2d': 4}}
            for i in range(3)
        ]
        sig1 = self._make_sig(sketches)
        sig2 = self._make_sig(sketches)
        scores = se.compute_raw_scores(sig1, sig2)
        unavailable = scores.get('_unavailable_criteria', [])
        self.assertIn('constraint_2d_similarity', unavailable)
        self.assertIn('constraint_ratio_similarity', unavailable)
        self.assertNotIn('constraint_2d_similarity', scores)
        self.assertNotIn('constraint_ratio_similarity', scores)

    def test_criteria_unavailable_when_coverage_below_80pct(self):
        """Se solo il 50% degli sketch ha vincoli -> criteri 11 & 12 unavailable."""
        sketches = [
            {'name': 'S0', 'geometry_count': 4, 'constraint_count': 3,
             'constraint_types': {'Coincidente': 3}, 'geometry_types': {'Line2d': 4}},
            {'name': 'S1', 'geometry_count': 4, 'constraint_count': 0,
             'constraint_types': {}, 'geometry_types': {'Line2d': 4}},
        ]
        sig1 = self._make_sig(sketches)
        sig2 = self._make_sig(sketches)
        scores = se.compute_raw_scores(sig1, sig2)
        unavailable = scores.get('_unavailable_criteria', [])
        self.assertIn('constraint_2d_similarity', unavailable)
        self.assertIn('constraint_ratio_similarity', unavailable)

    def test_criteria_available_at_exactly_80pct(self):
        """Soglia esatta: 80% degli sketch con vincoli -> criteri disponibili."""
        sketches = [
            {'name': f'S{i}', 'geometry_count': 4,
             'constraint_count': 3 if i < 4 else 0,
             'constraint_types': {'Coincidente': 3} if i < 4 else {},
             'geometry_types': {'Line2d': 4}}
            for i in range(5)  # 4/5 = 80%
        ]
        sig1 = self._make_sig(sketches)
        sig2 = self._make_sig(sketches)
        scores = se.compute_raw_scores(sig1, sig2)
        unavailable = scores.get('_unavailable_criteria', [])
        self.assertNotIn('constraint_2d_similarity', unavailable)
        self.assertNotIn('constraint_ratio_similarity', unavailable)

    def test_criteria_unavailable_when_one_sig_has_no_sketches(self):
        """Se una firma non ha sketch -> criteri 11 & 12 unavailable."""
        sketches = [
            {'name': 'S0', 'geometry_count': 4, 'constraint_count': 3,
             'constraint_types': {'Coincidente': 3}, 'geometry_types': {'Line2d': 4}},
        ]
        sig1 = self._make_sig(sketches)
        sig2 = self._make_sig([])  # nessuno sketch
        scores = se.compute_raw_scores(sig1, sig2)
        unavailable = scores.get('_unavailable_criteria', [])
        self.assertIn('constraint_2d_similarity', unavailable)
        self.assertIn('constraint_ratio_similarity', unavailable)

    def test_combine_scores_renormalises_weights_when_criteria_unavailable(self):
        """combine_scores rinormalizza i pesi quando i criteri vincoli sono unavailable."""
        raw = {
            'author_match': 0.0,
            'feature_count_similarity': 0.8,
            'feature_type_similarity': 0.7,
            'style_similarity': 0.6,
            'bigram_similarity': 0.5,
            'trigram_similarity': 0.4,
            'lcs_similarity': 0.9,
            'geometry_2d_similarity': 0.85,
            '_unavailable_criteria': [
                'feature_names_similarity',
                'constraint_2d_similarity',
                'constraint_ratio_similarity',
                'sketch_parametric_similarity',
            ],
            '_sketch_matches': [],
            '_constraint_coverage': {'sig1': 0.0, 'sig2': 0.0},
        }
        weights = dict(se.DEFAULT_WEIGHTS)
        combined = se.combine_scores(raw, weights)
        # overall deve essere un float finito in [0,1]
        self.assertTrue(0.0 <= combined['overall'] <= 1.0)
        # I criteri unavailable devono essere in excluded
        for crit in ['constraint_2d_similarity', 'constraint_ratio_similarity']:
            self.assertIn(crit, combined.get('_excluded_criteria', []))

    def test_diagnostic_metadata_always_present(self):
        """_constraint_coverage deve essere sempre presente in raw_scores."""
        sig = se.FeatureSignature(filename='a.par', filepath='a.par', file_hash='h')
        scores = se.compute_raw_scores(sig, sig)
        self.assertIn('_constraint_coverage', scores)
        cov = scores['_constraint_coverage']
        self.assertIn('sig1', cov)
        self.assertIn('sig2', cov)


if __name__ == '__main__':
    unittest.main()
