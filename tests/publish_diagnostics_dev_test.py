"""Diagnostic publisher scope, provenance, and exact-build symbol retention."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import publish_diagnostics_dev as diagnostic
import publish_reliability_dev_test as regression

class WindowsBuildRegressionTests(regression.PublishReliabilityDevTest):
    def setUp(self):
        self.original = regression.publisher
        regression.publisher = diagnostic
    def tearDown(self):
        regression.publisher = self.original

class DiagnosticPublisherTests(unittest.TestCase):
    def test_exact_validated_commits_and_versions(self):
        self.assertEqual(set(diagnostic.SOURCE), {'scale', 'display', 'touchscreen'})
        self.assertEqual(diagnostic.SOURCE['scale']['ref'], '212ea4f0579d4070d0e48921c32d678cb3ebbba4')
        self.assertEqual(diagnostic.SOURCE['display']['ref'], '55a70ebf60f8450d70ba6cd730e60b93bede3156')
        self.assertEqual(diagnostic.SOURCE['touchscreen']['ref'], diagnostic.SOURCE['display']['ref'])
        self.assertEqual([diagnostic.SOURCE[d]['version'] for d in diagnostic.SOURCE], ['V1.4.22','V1.4.9','V1.4.26'])
        self.assertEqual(diagnostic.SOURCE['touchscreen']['subproject'], 'touchscreen')
        self.assertFalse(any('touchscreen43' in f or 'touchpour' in f for f in diagnostic.expected_release_files()))

    def test_missing_private_key_stops_before_git_or_feed_mutations(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(diagnostic, 'git') as git:
            with self.assertRaisesRegex(RuntimeError, 'private key not found'):
                diagnostic.preflight(Path(tmp)/'missing.pem', {})
            git.assert_not_called()

    def test_symbols_survive_worktree_removal_and_match_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)/'worktree'; build=project/'build'; build.mkdir(parents=True)
            spec=diagnostic.SOURCE['scale']; app=spec['binary']
            (build/(app+'.bin')).write_bytes(b'published-image')
            (build/(app+'.elf')).write_bytes(b'exact-debug-symbols')
            (project/'sdkconfig').write_text('CONFIG_HEAP_USE_HOOKS=y\n')
            destination=Path(tmp)/'retained'
            diagnostic.save_debug_symbols('scale', spec, project, destination)
            digest=hashlib.sha256(b'published-image').hexdigest()
            folder=destination/spec['ref']/'scale'/digest
            identity=json.loads((folder/'BUILD_IDENTITY.json').read_text())
            self.assertEqual(identity['binary_sha256'],digest)
            self.assertEqual(identity['commit'],spec['ref'])
            self.assertEqual((folder/(app+'.elf')).read_bytes(),b'exact-debug-symbols')
            self.assertTrue((folder/'sdkconfig').is_file())

    def test_missing_elf_refuses_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp); (project/'build').mkdir()
            (project/'build/keg_scale_esp.bin').write_bytes(b'image')
            with self.assertRaisesRegex(RuntimeError,'Missing application/debug symbols'):
                diagnostic.save_debug_symbols('scale',diagnostic.SOURCE['scale'],project,project/'retained')

    def test_prepare_passes_selected_signing_directory_and_dev_scope(self):
        with patch.object(diagnostic,'command') as command:
            diagnostic.prepare('scale',diagnostic.SOURCE['scale'],Path('/scale'),Path('/display'),Path('/local-private-keys'))
        args=command.call_args.args[0]
        self.assertEqual(args[args.index('--channel')+1],'dev')
        self.assertEqual(args[args.index('--signing-key-dir')+1],Path('/local-private-keys'))
        self.assertIn('--require-signatures',args)

if __name__=='__main__': unittest.main()
