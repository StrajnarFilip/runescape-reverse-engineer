"""Timing evidence must preserve misses, session boundaries and unmatched keys."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('calibration_analysis', Path(__file__).resolve().parents[2] / 'work' / 'analyze_telos_calibration.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
MS = 1000000


def row(time_ms, active='melee'):
    return {'pid':1,'process_creation_time':2,'observed_ns':time_ms*MS,
            'combat':{'scene_epoch':3,'active_prayer':active},
            'animations':[{'name':'Telos, the Warden','instance_id':4}],
            'npc_action_events':[],'player_hit_events':[],'manual_prayer_keys':[]}


class CalibrationAnalysisTests(unittest.TestCase):
    def test_same_tick_miss_is_not_silently_lost_or_counted_as_damage(self):
        r=row(1000)
        r['npc_action_events']=[{'pid':1,'process_creation_time':2,'scene_epoch':3,'instance_id':4,
                                'sequence':1,'observed_ns':1000*MS,'action_sequence':28929}]
        r['player_hit_events']=[{'pid':1,'process_creation_time':2,'scene_epoch':3,'sequence':1,
                                'observed_ns':1000*MS-5000,'hit':{'type_id':482,'amount':0}}]
        result=analysis.summarize([r])
        self.assertEqual(len(result['cue_hit_pairs']),1)
        self.assertEqual(result['cue_to_observed_damage_delay']['count'],0)
        self.assertEqual(result['cue_to_observed_miss_delay']['count'],1)

    def test_restarted_scene_hit_cannot_be_assigned_to_old_cue(self):
        r=row(1000)
        r['npc_action_events']=[{'pid':1,'process_creation_time':2,'scene_epoch':3,'instance_id':4,
                                'sequence':1,'observed_ns':1000*MS,'action_sequence':28929}]
        r['player_hit_events']=[{'pid':1,'process_creation_time':2,'scene_epoch':4,'sequence':1,
                                'observed_ns':1600*MS,'hit':{'type_id':133,'amount':800}}]
        self.assertEqual(analysis.summarize([r])['cue_hit_pairs'],[])

    def test_only_a_matching_observed_overhead_change_acknowledges_key(self):
        r=row(1000)
        r['manual_prayer_keys']=[{'prayer':'soul_split','observed_ns':1010*MS,'previous_poll_ns':1000*MS,
                                 'virtual_key':114,'modifiers':1},
                                {'prayer':'ranged','observed_ns':1020*MS,'previous_poll_ns':1010*MS,
                                 'virtual_key':113,'modifiers':1}]
        result=analysis.summarize([r,row(1300,'soul_split')])
        self.assertEqual(result['manual_key_events'],2)
        self.assertEqual(len(result['matched_keyboard_changes']),1)
        self.assertEqual(result['sampled_key_to_overhead_delay']['median_ms'],290)


    def test_one_overhead_change_does_not_acknowledge_two_presses(self):
        r=row(1000)
        r['manual_prayer_keys']=[{'prayer':'soul_split','observed_ns':t*MS,'previous_poll_ns':(t-10)*MS,
                                 'virtual_key':114,'modifiers':1} for t in (1010,1100)]
        result=analysis.summarize([r,row(1300,'soul_split')])
        self.assertEqual(result['manual_key_events'],2)
        self.assertEqual(len(result['matched_keyboard_changes']),1)


if __name__=='__main__':
    unittest.main()
