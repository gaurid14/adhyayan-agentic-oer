// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title DecisionAudit
 * @notice Immutable audit trail for content-approval decisions in Adhyayan OER.
 *
 * Every time the Decision Agent selects (or rejects) content for a chapter,
 * a record is pushed here.  The on-chain record can never be altered or deleted,
 * giving contributors verifiable proof that the process was fair.
 *
 * Design notes
 * ────────────
 * - One chapter can have many decisions over time (array mapping).
 * - `compositeScore` is stored ×100 (e.g. 7.85 → 785) to avoid floats.
 * - `status`:  1 = approved,  2 = rejected,  3 = no_candidates.
 * - `selectedUploadId` is 0 when no upload was selected (rejection / no candidates).
 */
contract DecisionAudit {

    // ── Storage ──────────────────────────────────────────────────────────

    struct Decision {
        uint256 chapterId;
        uint256 selectedUploadId;
        uint256 compositeScore;     // ×100
        uint8   status;             // 1=approved  2=rejected  3=no_candidates
        uint256 timestamp;          // block.timestamp
    }

    /// chapterId → ordered list of decisions
    mapping(uint256 => Decision[]) public chapterDecisions;

    // ── Events ───────────────────────────────────────────────────────────

    event DecisionRecorded(
        uint256 indexed chapterId,
        uint256 selectedUploadId,
        uint256 compositeScore,
        uint8   status,
        uint256 timestamp
    );

    // ── Write ────────────────────────────────────────────────────────────

    /**
     * @notice Store a new decision for `chapterId`.
     * @param chapterId        ID of the chapter being decided on.
     * @param selectedUploadId Upload that was selected (0 if none).
     * @param compositeScore   Final composite score ×100.
     * @param status           1 approved · 2 rejected · 3 no_candidates.
     */
    function recordDecision(
        uint256 chapterId,
        uint256 selectedUploadId,
        uint256 compositeScore,
        uint8   status
    ) public {
        chapterDecisions[chapterId].push(Decision(
            chapterId,
            selectedUploadId,
            compositeScore,
            status,
            block.timestamp
        ));

        emit DecisionRecorded(
            chapterId,
            selectedUploadId,
            compositeScore,
            status,
            block.timestamp
        );
    }

    // ── Read ─────────────────────────────────────────────────────────────

    /**
     * @notice How many decisions exist for a chapter.
     */
    function getDecisionCount(uint256 chapterId) public view returns (uint256) {
        return chapterDecisions[chapterId].length;
    }

    /**
     * @notice Retrieve a specific decision by chapter + index.
     */
    function getDecision(uint256 chapterId, uint256 index)
        public
        view
        returns (
            uint256 selectedUploadId,
            uint256 compositeScore,
            uint8   status,
            uint256 timestamp
        )
    {
        Decision memory d = chapterDecisions[chapterId][index];
        return (d.selectedUploadId, d.compositeScore, d.status, d.timestamp);
    }
}
