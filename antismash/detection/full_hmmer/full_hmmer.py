# License: GNU Affero General Public License v3 or later
# A copy of GNU AGPL v3 should have been included in this software package in LICENSE.txt.

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from antismash.common.secmet.feature import FeatureLocation, PFAMDomain
from antismash.common.module_results import ModuleResults
from antismash.config import get_config

from .name2pfamid import NAME_TO_PFAMID


class FullHmmerResults(ModuleResults):
    schema_version = 1

    def __init__(self, record_id: str, evalue: float, score: float,
                 database: str) -> None:
        super().__init__(record_id)
        self.hits = []  # type: List[Dict[str, Any]]
        self.evalue = evalue
        self.score = score
        self.database = database

    def to_json(self) -> Dict[str, Any]:
        json = {"hits": self.hits, "record id": self.record_id,
                "schema": self.schema_version, "max evalue": self.evalue,
                "min score": self.score, "database": self.database}
        return json

    @staticmethod
    def from_json(json, record) -> Optional["FullHmmerResults"]:
        options = get_config()

        if record.id != json.get("record id"):
            logging.warning("FullHmmer results are for different record, discarding previous results")
            return None

        if json.get("schema") != FullHmmerResults.schema_version:
            logging.warning("FullHmmer results are for different result schema, discarding previous results")
            return None

        if json.get("database") != options.database_dir:
            logging.warning("FullHmmer database location has changed, discarding previous results")
            return None

        evalue = json.get("max evalue")
        score = json.get("min score")
        if evalue is None or score is None:
            raise ValueError("Invalid FullHmmer results values")
        assert isinstance(score, float) and isinstance(evalue, float)

        # if the current options have expanded the detection range, rerunning is required
        if evalue < options.fh_max_evalue:
            logging.debug("Discarding FullHmmer results, using new evalue threshold")
            return None
        if score > options.fh_min_score:
            logging.debug("Discarding FullHmmer results, using new score threshold")
            return None

        hits = json.get("hits")
        if not isinstance(hits, list):
            raise TypeError("FullHmmer results contain unexpected types")
        # if the thresholds changed, trim out any extra hits here
        hits = [hit for hit in hits if hit["score"] >= options.fh_min_score and hit["evalue"] <= options.fh_max_evalue]

        results = FullHmmerResults(record.id, options.fh_max_evalue, options.fh_min_score,
                                   json["database"])
        results.hits = hits

        return results

    def add_to_record(self, record):
        for i, hit in enumerate(self.hits):
            pfam = PFAMDomain(FeatureLocation(hit["start"], hit["end"], hit["strand"]),
                              description=hit["description"])
            for key in ["label", "locus_tag", "domain", "evalue",
                        "score", "database", "translation", "db_xref"]:
                setattr(pfam, key, hit[key])
            pfam.tool = "fullhmmer"
            pfam.detection = "hmmscan"
            pfam.domain_id = "fullhmmer_{}_{:04d}".format(pfam.locus_tag, i + 1)
            record.add_pfam_domain(pfam)

            # TODO: determine whether to keep this note in the parent feature or not
#            feature.notes.append("%s-Hit: %s. Score: %s. E-value: %s. Domain range: %s..%s." %
#                                 (pfam.database, pfam.domain, pfam.score,
#                                  pfam.evalue, hsp.hit_start, hsp.hit_end))


def calculate_start_and_end(feature, result) -> Tuple[int, int]:
    "Calculate start and end of a result"
    if feature.strand == 1:
        start = feature.location.start + (3 * result.query_start)
        end = feature.location.start + (3 * result.query_end)
    else:
        end = feature.location.end - (3 * result.query_start)
        start = feature.location.end - (3 * result.query_end)

    return start, end


def build_hits(record, options, results) -> List[Dict[str, Any]]:
    "Annotate record with CDS_motifs for the result"
    logging.debug("Generating feature objects for PFAM hits")

    hits = []

    feature_by_id = record.get_cds_name_mapping()

    for result in results:
        for hsp in result.hsps:
            if hsp.bitscore <= options.fh_min_score or hsp.evalue >= options.fh_max_evalue:
                continue

            if hsp.query_id not in hsp.query_id:
                continue

            feature = feature_by_id[hsp.query_id]

            start, end = calculate_start_and_end(feature, hsp)

            dummy_feature = PFAMDomain(FeatureLocation(start, end, feature.location.strand),
                                       description="")

            pfam = {"start": start, "end": end, "strand": feature.location.strand,
                    "label": result.id, "locus_tag": feature.locus_tag,
                    "domain": hsp.hit_id, "evalue": hsp.evalue, "score": hsp.bitscore,
                    "database": os.path.basename(result.target),
                    "translation": str(dummy_feature.extract(record.seq).translate(table=feature.transl_table)),
                    "db_xref": [NAME_TO_PFAMID.get(hsp.hit_id)],
                    "description": hsp.hit_description}
            hits.append(pfam)
    return hits


def generate_results(record, hmmscan_results, options) -> FullHmmerResults:
    results = FullHmmerResults(record.id, options.fh_max_evalue,
                               options.fh_min_score, options.database_dir)
    results.hits = build_hits(record, options, hmmscan_results)
    return results