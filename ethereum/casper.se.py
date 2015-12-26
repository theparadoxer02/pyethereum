data nextUserPos # map to storage index 0
data users[2**50](address, deposit_size, orig_deposit_size, induction_height, withdrawal_height, validationCode, seq, prevhash, pos, blockhashes[2**50], stateroots[2**50], probs[2**50], profits[2**50])
data deletedUserPositions[2**50]
data nextDeletedUserPos
data userPosToIndexMap[2**50]
data nextUserIndex
data activeValidators

macro MIN_DEPOSIT: 1500 * 10**18

macro MAX_DEPOSIT: 60000 * 10**18

macro ENTER_EXIT_DELAY: 110

macro WITHDRAWAL_WAITTIME: 20

macro SCORING_REWARD_DIVISOR: 10**16 # 300000 * 10**9 * 300000 * 10**9 / 10**13 / 10**16 ~= 1, so a max probability bet gone wrong is a full slashing

macro MAX_INCENTIVIZATION_DEPTH: 160

macro PROFIT_PACKING_NUM: 20

macro PROFIT_PACKING_BYTES: 10

macro VALIDATOR_ROUNDS: 5

macro maskinclude($top, $bottom):
    256**$top - 256**$bottom

macro maskexclude($top, $bottom):
    ~not(256**$top - 256**$bottom)

macro mcopy_small($to, $frm, $bytes):
    ~mstore($to, (~mload($to) & sub(0, 256**$bytes)) + ($frm & (256**$bytes - 1)))


# Become a validator
def join(validationCode:bytes):
    assert msg.value >= MIN_DEPOSIT and msg.value <= MAX_DEPOSIT
    if self.nextDeletedUserPos:
        userPos = self.deletedUserPositions[self.nextDeletedUserPos - 1]
        self.nextDeletedUserPos -= 1
    else:
        userPos = self.nextUserPos
        self.nextUserPos = userPos + 1
    userIndex = self.nextUserIndex
    self.userPosToIndexMap[userPos] = userIndex
    self.nextUserIndex = userIndex + 1
    self.users[userIndex].address = msg.sender
    self.users[userIndex].pos = userPos
    ~sstorebytes(ref(self.users[userIndex].validationCode), validationCode, len(validationCode))
    # log(20, ~ssize(self.users[userIndex].validationCode))
    self.users[userIndex].deposit_size = msg.value
    self.users[userIndex].orig_deposit_size = msg.value
    self.users[userIndex].induction_height = if(block.number, block.number + ENTER_EXIT_DELAY, 0)
    self.users[userIndex].withdrawal_height = 2**100
    return(userIndex:uint256)


# Leave the validator pool
def withdraw(index:uint256):
    if self.users[index].withdrawal_height + WITHDRAWAL_WAITTIME <= block.number:
        send(self.users[index].address, self.users[index].deposit_size)
        self.users[index].address = 0
        self.users[index].deposit_size = 0
        self.deletedUserPositions[self.nextDeletedUserPos] = self.users[index].pos
        self.nextDeletedUserPos += 1
        return(1:bool)
    return(0:bool)


# Submit a bet
def submitBet(index:uint256, max_height:uint256, probs:bytes, blockhashes:bytes32[], stateroots:bytes32[], prevhash:bytes32, seqnum:uint256, sig:bytes):
    if seqnum != self.users[index].seq:
        log3(20, index, block.number, seqnum, self.users[index].seq)
    assert seqnum == self.users[index].seq
    assert prevhash == self.users[index].prevhash
    assert max_height <= block.number
    assert block.number > seqnum * VALIDATOR_ROUNDS + (index % VALIDATOR_ROUNDS)
    assert self.users[index].withdrawal_height > block.number
    assert len(probs) >= len(blockhashes)
    assert len(probs) >= len(stateroots)
    # Compute the signature hash
    _calldata = string(~calldatasize())
    ~calldatacopy(_calldata, 0, ~calldatasize())
    signing_hash = ~sha3(_calldata, ~calldatasize() - 32 - ceil32(len(sig)))
    self.users[index].prevhash = ~sha3(_calldata, ~calldatasize())
    self.users[index].seq = seqnum + 1
    # Check the sig against the user validation code
    user_validation_code = string(~ssize(ref(self.users[index].validationCode)))
    ~sloadbytes(ref(self.users[index].validationCode), user_validation_code, len(user_validation_code))
    sig_verified = 0
    with L = len(sig):
        sig[-1] = signing_hash
        ~callstatic(msg.gas - 20000, user_validation_code, len(user_validation_code), sig - 32, L + 32, ref(sig_verified), 32)
        sig[-1] = L
    assert sig_verified == 1
    # Bet with max height 2**256 - 1 to start withdrawal
    if max_height == ~sub(0, 1):
        self.users[index].withdrawal_height = block.number
        return True
    # Update blockhashes, storing blockhash correctness info in groups of 32
    i = 0
    v = self.users[index].blockhashes[max_height / 32]
    while i < len(blockhashes):
        with h = max_height - i:
            byte = not(not(~blockhash(h))) * 2 + (blockhashes[i] == ~blockhash(h))
            with offset = h % 32:
                v = (v & maskexclude(offset + 1, offset)) + byte * 256**offset
                if offset == 0 or i == len(blockhashes) - 1:
                    self.users[index].blockhashes[h / 32] = v
                    v = self.users[index].blockhashes[(h / 32) - 1]
        i += 1
    # Update stateroots, storing stateroot correctness info in groups of 32
    i = 0
    v = self.users[index].stateroots[max_height / 32]
    while i < len(stateroots):
        with h = max_height - i:
            byte = not(not(~stateroot(h))) * 2 + (stateroots[i] == ~stateroot(h))
            with offset = h % 32:
                v = (v & maskexclude(offset + 1, offset)) + byte * 256**offset
                if offset == 0 or i == len(stateroots) - 1:
                    self.users[index].stateroots[h / 32] = v
                    v = self.users[index].stateroots[(h / 32) - 1]
        i += 1
    # Update probabilities; paste the probs into the self.users[index].probs
    # array at the correct positions, assuming the probs array stores probs
    # in groups of 32
    h = max_height + 1
    i = 0
    while i < len(probs):
        with top = (h % 32) or 32:
            with bottom = max(top - len(probs) + i, 0):
                x = (self.users[index].probs[(h - 1) / 32] & maskexclude(top, bottom)) + (~mload(probs + i - 32 + top) & maskinclude(top, bottom))
                self.users[index].probs[(h - 1) / 32] = x
                h -= top
                i += top

    minChanged = max_height - max(max(len(blockhashes), len(stateroots)), len(probs)) + 1
    # Incentivization
    with H = max(max(self.users[index].induction_height, block.number - MAX_INCENTIVIZATION_DEPTH), minChanged):
        netProb = 10**9
        with PROFITBLOCK = string(PROFIT_PACKING_NUM * PROFIT_PACKING_BYTES):
            ~sloadbytes(ref(self.users[index].profits[div(H - 1, PROFIT_PACKING_NUM)]), PROFITBLOCK, PROFIT_PACKING_NUM * PROFIT_PACKING_BYTES)
            CURPROFIT = mod(~mload(PROFITBLOCK - 32 + PROFIT_PACKING_BYTES * mod(H - 1, PROFIT_PACKING_NUM) * PROFIT_PACKING_BYTES), 256**PROFIT_PACKING_BYTES)
            ~sloadbytes(ref(self.users[index].profits[div(H, PROFIT_PACKING_NUM)]), PROFITBLOCK, PROFIT_PACKING_NUM * PROFIT_PACKING_BYTES)
            while H <= max_height:
                # Determine the byte that was saved as the probability
                # Convert the byte to odds * 1 billion
                with blockOdds = convertProbReprToOdds(getch(probs, max_height - H)): # mod((self.users[index].probs[H / 32] / 256**(H % 32)), 256) or 128
                    blockHashInfo = mod(div(self.users[index].blockhashes[H / 32], 256**(H % 32)), 256)
                    stateRootInfo = mod(div(self.users[index].stateroots[H / 32], 256**(H % 32)), 256)
                    netProb = netProb * convertOddsToProb(blockOdds) / 10**9
            
                    if blockOdds >= 10**9 and blockHashInfo >= 2:
                        profitFactor = if(blockHashInfo % 2, scoreCorrect(blockOdds), scoreIncorrect(blockOdds))
                    else:
                        profitFactor = if(blockHashInfo % 2 or blockHashInfo == 0, scoreCorrect(10**18 / blockOdds), scoreIncorrect(10**18 / blockOdds))
            
                    # Check if the state root bet that was made is correct.
                    if stateRootInfo >= 2:
                        if stateRootInfo % 2:
                            profitFactor2 = scoreCorrect(convertProbToOdds(netProb))
                        else:
                            profitFactor2 = scoreIncorrect(convertProbToOdds(netProb))
                    else:
                        profitFactor2 = 0
                    # If we lose a lot of money, log it for debug purposes
                    if profitFactor < 0:
                        if blockOdds < 10**8 or blockOdds > 10**10:
                            log3(700 + index, H, blockHashInfo, blockOdds, -profitFactor)
                        if blockOdds < 10000 or blockOdds > 10**14:
                            log0(1, 3141592653589)
                    if profitFactor2 < 0:
                        stateOdds = convertProbToOdds(netProb)
                        if stateOdds < 10**8 or stateOdds > 10**10:
                            log3(800 + index, H, stateRootInfo, stateOdds, -profitFactor2)
                        if stateOdds < 10000 or stateOdds > 10**14:
                            log0(1, 3141592653589)
                    # Update the profit counter
                    CURPROFIT += profitFactor2 + profitFactor
                    # log2(4, H, profitFactor2 + profitFactor, CURPROFIT)
                    mcopy_small(PROFITBLOCK + PROFIT_PACKING_BYTES * mod(H, PROFIT_PACKING_NUM) - 32 + PROFIT_PACKING_BYTES, CURPROFIT, PROFIT_PACKING_BYTES)
                    if (mod(H, PROFIT_PACKING_NUM) == (PROFIT_PACKING_NUM - 1) or H == max_height):
                        ~sstorebytes(ref(self.users[index].profits[div(H, PROFIT_PACKING_NUM)]), PROFITBLOCK, PROFIT_PACKING_NUM * PROFIT_PACKING_BYTES)
                        ~sloadbytes(ref(self.users[index].profits[(H + 1) / PROFIT_PACKING_NUM]), PROFITBLOCK, PROFIT_PACKING_NUM * PROFIT_PACKING_BYTES)
                    # self.users[index].profits[H] = profitFactor + profitFactor2 + self.users[index].profits[H - 1]
                    # log3(1000 + H, msg.gas, blockOdds, profitFactor, profitFactor2)
                    # log3(1001, block.number, H, blockHashInfo, stateRootInfo)
                    H += 1
            if H >= MAX_INCENTIVIZATION_DEPTH:
                ~sloadbytes(ref(self.users[index].profits[(H - MAX_INCENTIVIZATION_DEPTH) / PROFIT_PACKING_NUM]), PROFITBLOCK, PROFIT_PACKING_NUM * PROFIT_PACKING_BYTES)
                prevProfit = mod(~mload(PROFITBLOCK + PROFIT_PACKING_BYTES * mod(H, PROFIT_PACKING_NUM) - 32 + PROFIT_PACKING_BYTES), 256**PROFIT_PACKING_BYTES)
            else:
                prevProfit = 0
            # log3(90, prevProfit, CURPROFIT, blksActive, self.users[index].deposit_size)
            profit = self.users[index].deposit_size * signextend(PROFIT_PACKING_BYTES, CURPROFIT - prevProfit) / SCORING_REWARD_DIVISOR
            # log0(100, profit)
            # Optional verification code
            # h = max_height
            # while h >= 0:
            #     log0(10000 + h, getProfit(index, h))
            #     h -= 1
            # log1(51, 54, profit)
            # log0(1000 * block.number, profit)
            # log1(1000 + H, netProb, profitFactor2)
            self.users[index].deposit_size = max(0, self.users[index].deposit_size + profit)
            # log3(500 + index, block.number, profitFactor + profitFactor2, CURPROFIT, prevProfit)
            log4(500 + index, seqnum, len(probs), len(blockhashes), len(stateroots), txexecgas() - msg.gas)
            return(1:bool)

# Interpret prob as odds in scientific notation: 5 bit exponent
# (-16….15), 3 bit mantissa (1….1.875). Convert to odds per billion
# This allows 3.125% granularity, with odds between 65536:1 against
# and 1:61440 for
macro convertProbReprToOdds($probRepr):
    2**(($probRepr + 5) / 7) * (7 + ($probRepr + 5) % 7) * 272

macro convertOddsToProb($odds):
    $odds * 10**9 / (10**9 + $odds)

macro convertProbToOdds($prob):
    $prob * 10**9 / (10**9 - $prob)


# This is a simple quadratic scoring rule.
macro scoreCorrect($odds):
    $odds / 10000


macro scoreIncorrect($odds):
    (0 - $odds * $odds / 10**13)


# Randomly select a validator using a las vegas algorithm
def const sampleValidator(orig_seedhash:bytes32, blknumber:uint256):
    n = mod(orig_seedhash, 2**64)
    seedhash = sha3([orig_seedhash, blknumber]:arr)
    while 1:
        with index = self.userPosToIndexMap[mod(seedhash, n)]:
            if (div(seedhash, 2**128) * MAX_DEPOSIT / 2**128 < self.users[index].orig_deposit_size):
                if blknumber >= self.users[index].induction_height and blknumber <= self.users[index].withdrawal_height:
                    return(self.userPosToIndexMap[index])
        seedhash = sha3(seedhash)


# Getter methods 
def const getNextUserPos():
    return(self.nextUserPos:uint256)

def const getUserAtPosition(pos:uint256):
    return(self.userPosToIndexMap[pos]:uint256)

def const getUserStatus(i:uint256):
    if not self.users[i].address: # inactive
        return 0
    elif block.number < self.users[i].induction_height: # not yet inducted
        return 1
    elif block.number < self.users[i].withdrawal_height: # now inducted
        return 2
    else: # withdrawing
        return 3

def const getUserAddress(i:uint256):
    return(self.users[i].address:address)

def const getUserInductionHeight(i:uint256):
    return(self.users[i].induction_height:uint256)

def const getUserWithdrawalHeight(i:uint256):
    return(self.users[i].withdrawal_height:uint256)

def const getUserValidationCode(i:uint256):
    a = string(~ssize(ref(self.users[i].validationCode)))
    ~sloadbytes(ref(self.users[i].validationCode), a, len(a))
    return(a:str)

def const getUserSeq(i:uint256):
    return(self.users[i].seq:uint256)

def const getUserPrevhash(i:uint256):
    return(self.users[i].prevhash:bytes32)

def const getValidatorSignups():
    return(self.nextUserIndex:uint256)

def const getUserDeposit(i:uint256):
    return(self.users[i].deposit_size:uint256)
