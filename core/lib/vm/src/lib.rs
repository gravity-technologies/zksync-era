#![deny(unreachable_pub)]
#![deny(unused_crate_dependencies)]
#![warn(unused_extern_crates)]
#![warn(unused_imports)]

pub use old_vm::memory::SimpleMemory;
pub use oracles::storage::StorageOracle;

pub use errors::{
    BytecodeCompressionError, Halt, TxRevertReason, VmRevertReason, VmRevertReasonParsingError,
};

pub use tracers::{
    call::CallTracer,
    traits::{BoxedTracer, DynTracer, ExecutionEndTracer, ExecutionProcessing, VmTracer},
    utils::VmExecutionStopReason,
    StorageInvocations, ValidationError, ValidationTracer, ValidationTracerParams,
};

pub use types::{
    inputs::{L1BatchEnv, L2BlockEnv, SystemEnv, TxExecutionMode, VmExecutionMode},
    internals::ZkSyncVmState,
    outputs::{
        BootloaderMemory, CurrentExecutionState, ExecutionResult, FinishedL1Batch, L2Block,
        Refunds, VmExecutionResultAndLogs, VmExecutionStatistics,
    },
};
pub use utils::transaction_encoding::TransactionVmExt;

pub use bootloader_state::BootloaderState;

pub use crate::vm::{Snapshot, Vm};

mod bootloader_state;
mod errors;
mod implementation;
mod old_vm;
mod oracles;
mod rollback;
mod tracers;
mod types;
mod vm;

pub mod constants;
pub mod utils;

#[cfg(test)]
mod tests;
