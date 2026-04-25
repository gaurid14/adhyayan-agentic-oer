// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title AdhyayanCertificate
 * @notice Issues tamper-proof, verifiable certificates on the Ethereum blockchain
 *         for Adhyayan OER platform.
 *
 * Two certificate types are supported:
 *   0 = STUDENT   — awarded when a student completes an entire course
 *   1 = CONTRIBUTOR — awarded when a contributor's content chapter is officially released
 *
 * Each certificate stores:
 *   - recipientName  : Full name of the person
 *   - courseName     : The course or chapter name
 *   - issueType      : 0 for Student, 1 for Contributor
 *   - issuedAt       : block.timestamp at time of minting
 *
 * Token IDs start from 1 and auto-increment. Once minted, records are immutable.
 */
contract AdhyayanCertificate {

    // Certificate record stored on-chain
    struct Certificate {
        string  recipientName;
        string  courseName;
        uint8   issueType;     // 0 = STUDENT, 1 = CONTRIBUTOR
        uint256 issuedAt;
        bool    exists;
    }

    // Token ID counter (starts at 1)
    uint256 private _nextTokenId = 1;

    // Storage: tokenId => Certificate
    mapping(uint256 => Certificate) private _certificates;

    // Reverse lookup: allows finding all token IDs for a given name
    // (optional — kept lightweight for now)

    // Events
    event CertificateMinted(
        uint256 indexed tokenId,
        string  recipientName,
        string  courseName,
        uint8   issueType,
        uint256 issuedAt
    );

    /**
     * @notice Mint a new certificate. Returns the unique token ID.
     * @param recipientName  Full name of the recipient
     * @param courseName     Course or Chapter name
     * @param issueType      0 = STUDENT completion, 1 = CONTRIBUTOR release
     */
    function mintCertificate(
        string memory recipientName,
        string memory courseName,
        uint8         issueType
    ) external returns (uint256) {
        require(bytes(recipientName).length > 0, "Recipient name required");
        require(bytes(courseName).length > 0,    "Course name required");
        require(issueType <= 1,                  "Invalid issue type");

        uint256 tokenId = _nextTokenId;
        _nextTokenId++;

        _certificates[tokenId] = Certificate({
            recipientName : recipientName,
            courseName    : courseName,
            issueType     : issueType,
            issuedAt      : block.timestamp,
            exists        : true
        });

        emit CertificateMinted(tokenId, recipientName, courseName, issueType, block.timestamp);

        return tokenId;
    }

    /**
     * @notice Retrieve certificate details by token ID.
     */
    function getCertificate(uint256 tokenId)
        external
        view
        returns (
            string memory recipientName,
            string memory courseName,
            uint8         issueType,
            uint256       issuedAt
        )
    {
        Certificate storage cert = _certificates[tokenId];
        require(cert.exists, "Certificate does not exist");

        return (cert.recipientName, cert.courseName, cert.issueType, cert.issuedAt);
    }

    /**
     * @notice Check whether a certificate token exists.
     */
    function certificateExists(uint256 tokenId) external view returns (bool) {
        return _certificates[tokenId].exists;
    }

    /**
     * @notice Returns the next token ID that will be assigned.
     */
    function nextTokenId() external view returns (uint256) {
        return _nextTokenId;
    }
}
