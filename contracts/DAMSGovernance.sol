// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/**
 * @title DAMSGovernance
 * @notice Executable research reference for guild-scoped contribution weights.
 * Weight is floor(sqrt(C)), an integer approximation, not a continuous function
 * or a complete quadratic-voting mechanism. This contract has one trusted
 * administrator and public contribution records. It does not establish identity
 * uniqueness, input truth, privacy, decentralised attestation, MACI, or ZK proofs.
 * No real personnel records should be entered into this research prototype.
 */
contract DAMSGovernance {
    address public immutable admin;

    // One active guild per address. Credit remains in its issuing guild on exit.
    mapping(address => bytes32) public guild;
    mapping(bytes32 => mapping(address => uint256)) public contribution;
    mapping(bytes32 => mapping(address => uint256)) public nextNonce;
    mapping(bytes32 => uint256) public guildContribution;
    uint256 public totalContribution;

    mapping(bytes32 => address[]) private _members;
    mapping(bytes32 => mapping(address => uint256)) private _memberIndexPlusOne;
    mapping(bytes32 => uint256) private _denominator;

    struct Attestation {
        address member;
        bytes32 scope;
        uint256 amount;
        uint256 nonce;
        bool active;
    }
    // A consumed reference remains consumed after revocation.
    mapping(bytes32 => Attestation) public attestations;

    error NotAttester();
    error InvalidMember();
    error InvalidGuild();
    error AlreadyAssigned();
    error NotGuildMember();
    error InvalidAmount();
    error InvalidReference();
    error ReferenceConsumed();
    error InvalidNonce();
    error AttestationNotActive();
    error EmptyGuild();
    error NoAuthority();

    event ContributionAttested(
        address indexed member, bytes32 indexed scope, uint256 amount,
        bytes32 indexed attestationRef, uint256 nonce
    );
    event ContributionRevoked(bytes32 indexed attestationRef);
    event GuildAssigned(address indexed member, bytes32 indexed oldGuild, bytes32 indexed newGuild);

    constructor() { admin = msg.sender; }

    modifier onlyAttester() {
        if (msg.sender != admin) revert NotAttester();
        _;
    }

    /// @notice Administrator controls the canonical membership register.
    /// Old credit is neither moved to the new guild nor erased. Returning to the
    /// issuing guild reactivates unrevoked credit; this is an explicit policy.
    function assignGuild(address member, bytes32 scope) external onlyAttester {
        if (member == address(0)) revert InvalidMember();
        if (scope == bytes32(0)) revert InvalidGuild();
        bytes32 oldGuild = guild[member];
        if (oldGuild == scope) revert AlreadyAssigned();
        if (oldGuild != bytes32(0)) _removeMember(member, oldGuild);
        guild[member] = scope;
        _members[scope].push(member);
        _memberIndexPlusOne[scope][member] = _members[scope].length;
        _denominator[scope] += integerSqrt(contribution[scope][member]);
        emit GuildAssigned(member, oldGuild, scope);
    }

    function removeFromGuild(address member) external onlyAttester {
        bytes32 scope = guild[member];
        if (scope == bytes32(0)) revert NotGuildMember();
        _removeMember(member, scope);
        delete guild[member];
        emit GuildAssigned(member, scope, bytes32(0));
    }

    function _removeMember(address member, bytes32 scope) private {
        uint256 index = _memberIndexPlusOne[scope][member] - 1;
        address lastMember = _members[scope][_members[scope].length - 1];
        _members[scope][index] = lastMember;
        _memberIndexPlusOne[scope][lastMember] = index + 1;
        _members[scope].pop();
        delete _memberIndexPlusOne[scope][member];
        _denominator[scope] -= integerSqrt(contribution[scope][member]);
    }

    /// @notice A direct administrator transaction, not an off-chain signature.
    /// Reference uniqueness and the expected scope-member nonce reject retries
    /// and stale issuance. They cannot detect the same real-world work submitted
    /// under two different evidence references by a dishonest administrator.
    function attestContribution(
        address member, bytes32 scope, uint256 amount,
        bytes32 attestationRef, uint256 expectedNonce
    ) external onlyAttester {
        if (scope == bytes32(0)) revert InvalidGuild();
        if (member == address(0)) revert InvalidMember();
        if (guild[member] != scope) revert NotGuildMember();
        if (amount == 0) revert InvalidAmount();
        if (attestationRef == bytes32(0)) revert InvalidReference();
        if (attestations[attestationRef].member != address(0)) revert ReferenceConsumed();
        if (expectedNonce != nextNonce[scope][member]) revert InvalidNonce();

        uint256 previous = contribution[scope][member];
        uint256 updated = previous + amount; // Solidity checked arithmetic.
        nextNonce[scope][member] = expectedNonce + 1;
        contribution[scope][member] = updated;
        guildContribution[scope] += amount;
        totalContribution += amount;
        _denominator[scope] = _denominator[scope] - integerSqrt(previous) + integerSqrt(updated);
        attestations[attestationRef] = Attestation(member, scope, amount, expectedNonce, true);
        emit ContributionAttested(member, scope, amount, attestationRef, expectedNonce);
    }

    /// @notice Revokes the whole record; corrections require a new reference.
    /// This is an administrative correction primitive, not an appeal process.
    function revokeContribution(bytes32 attestationRef) external onlyAttester {
        Attestation storage record = attestations[attestationRef];
        if (!record.active) revert AttestationNotActive();
        uint256 previous = contribution[record.scope][record.member];
        uint256 updated = previous - record.amount;
        contribution[record.scope][record.member] = updated;
        guildContribution[record.scope] -= record.amount;
        totalContribution -= record.amount;
        if (guild[record.member] == record.scope) {
            _denominator[record.scope] = _denominator[record.scope]
                - integerSqrt(previous) + integerSqrt(updated);
        }
        record.active = false;
        emit ContributionRevoked(attestationRef);
    }

    function guildMembers(bytes32 scope) external view returns (address[] memory) {
        return _members[scope];
    }

    function authorityWeight(address member) public view returns (uint256) {
        bytes32 scope = guild[member];
        return scope == bytes32(0) ? 0 : integerSqrt(contribution[scope][member]);
    }

    /// @notice No caller-supplied list: cache equals the sum over the current
    /// canonical register, updated atomically on issuance, revocation and exit.
    function authorityDenominator(bytes32 scope) external view returns (uint256) {
        return _denominator[scope];
    }

    /// @notice Exact numerator/denominator pair for the integer policy.
    /// Empty or all-zero guilds have no contribution-authorised decisions.
    /// There is no hidden equal-weight or epsilon fallback. Bootstrap issuance
    /// remains a separate trusted-administrator power and is not a vote.
    function authorityFraction(bytes32 scope, address member)
        external view returns (uint256 numerator, uint256 denominator)
    {
        if (_members[scope].length == 0) revert EmptyGuild();
        if (guild[member] != scope) revert NotGuildMember();
        denominator = _denominator[scope];
        if (denominator == 0) revert NoAuthority();
        numerator = integerSqrt(contribution[scope][member]);
    }

    /// @notice Floor square root for the entire uint256 range, including max.
    /// After x >= 4, initial z = floor(x/2)+1 is an upper bound. Subsequent
    /// Babylonian updates decrease until the integer floor root is reached.
    /// Unlike (x+1)/2, the initial expression cannot overflow at uint256.max.
    function integerSqrt(uint256 x) public pure returns (uint256 y) {
        if (x == 0) return 0;
        if (x < 4) return 1;
        y = x;
        uint256 z = (x >> 1) + 1;
        while (z < y) {
            y = z;
            z = (x / z + z) >> 1;
        }
    }
}
