"""Cap evidence is narrower than lane-stop evidence; no provider calls."""
import copy,json,sqlite3,sys
from pathlib import Path
import pytest
C=Path(__file__).resolve().parents[1];sys.path[:0]=[str(C),str(C/'webui')]
from compute_parity.provisioning import pre_ready_infrastructure,cap_exemptions,remaining_attempts,states
from compute_parity.journal import Journal
from compute_parity.contracts import ParityError




def test_p1m2_missing_job_session_stays_counted():
    job={'request_id':'orphan','lane':'colab','state':'closed_no_execution_evidence','actual_cost':{'amount':0,'unit':'CU'},'artifacts':[]}
    assert not pre_ready_infrastructure(job,None)
    assert cap_exemptions([job],'/missing','fixture-owner',evidence={})=={}







