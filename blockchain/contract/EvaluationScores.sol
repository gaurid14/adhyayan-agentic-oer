// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract EvaluationScores {

    struct Scores {
        uint clarity;
        uint coherence;
        uint engagement;
        uint accuracy;
        uint completeness;
        uint timestamp;
    }

    mapping(uint => Scores) public uploadScores;

    event ScoresStored(
        uint uploadId,
        uint clarity,
        uint coherence,
        uint engagement,
        uint accuracy,
        uint completeness
    );

    function storeScores(
        uint uploadId,
        uint clarity,
        uint coherence,
        uint engagement,
        uint accuracy,
        uint completeness
    ) public {

        uploadScores[uploadId] = Scores(
            clarity,
            coherence,
            engagement,
            accuracy,
            completeness,
            block.timestamp
        );

        emit ScoresStored(
            uploadId,
            clarity,
            coherence,
            engagement,
            accuracy,
            completeness
        );
    }

    function getScores(uint uploadId)
        public
        view
        returns (
            uint,
            uint,
            uint,
            uint,
            uint,
            uint
        )
    {
        Scores memory s = uploadScores[uploadId];
        return (
            s.clarity,
            s.coherence,
            s.engagement,
            s.accuracy,
            s.completeness,
            s.timestamp
        );
    }
}