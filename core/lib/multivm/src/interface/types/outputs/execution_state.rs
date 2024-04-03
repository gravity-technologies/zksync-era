use zksync_types::{
    l2_to_l1_log::{SystemL2ToL1Log, UserL2ToL1Log},
    zk_evm_types::LogQuery,
    StorageLogQuery, VmEvent, U256,
};

/// State of the VM since the start of the batch execution.
#[derive(Debug, Clone, PartialEq)]
pub struct CurrentExecutionState {
    /// Events produced by the VM.
    pub events: Vec<VmEvent>,
    /// Storage logs produced by the VM.
    pub storage_log_queries: Vec<StorageLogQuery>,
    /// The deduplicated storage logs produced by the VM.
    /// It is the deduplicated version of the `storage_log_queries` field.
    pub deduplicated_storage_log_queries: Vec<LogQuery>,
    /// Hashes of the contracts used by the VM.
    pub used_contract_hashes: Vec<U256>,
    /// L2 to L1 logs produced by the VM.
    pub system_logs: Vec<SystemL2ToL1Log>,
    /// L2 to L1 logs produced by the L1Messeger.
    /// For pre-boojum VMs, there was no distinction between user logs and system
    /// logs and so all the outputted logs were treated as user_l2_to_l1_logs.
    pub user_l2_to_l1_logs: Vec<UserL2ToL1Log>,
    /// Number of log queries produced by the VM. Including l2_to_l1 logs, storage logs and events.
    pub total_log_queries: usize,
    /// Number of cycles used by the VM.
    pub cycles_used: u32,
    /// Sorted & deduplicated events logs for batch. Note, that this is a more "low-level" representation of
    /// the `events` field of this struct TODO(PLA-649): refactor to remove duplication of data.
    pub deduplicated_events_logs: Vec<LogQuery>,
    /// Refunds returned by `StorageOracle`.
    pub storage_refunds: Vec<u32>,
    /// Pubdata costs returned by `StorageOracle`.
    /// This field is only used since `v1.5.0`. For all the previous versions this field is an empty vector.
    pub pubdata_costs: Vec<i32>,
}

/// Bootloader Memory of the VM.
pub type BootloaderMemory = Vec<(usize, U256)>;
